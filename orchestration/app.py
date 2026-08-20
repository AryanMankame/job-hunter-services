from fastapi import FastAPI, UploadFile, Form, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
try:
    from mangum import Mangum
except ImportError:  # non-Lambda platforms (e.g. Vercel) run FastAPI's ASGI `app` directly
    Mangum = None
from common.database import DatabaseService
from common.helpers import verify_correct_email_format
from common.scoring import score_resume
from common.skills import SkillsMatcher
from pydantic import BaseModel
from typing import Optional
from bson import json_util
from bson.objectid import ObjectId
import json
import httpx
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

try:
    from ws_handler import handle_ws_event, push_to_user
except ImportError:
    from orchestration.ws_handler import handle_ws_event, push_to_user

try:
    from billing import (
        can_generate,
        get_entitlement,
        PLANS,
        RazorpayError,
        create_subscription_session,
        cancel_subscription,
        verify_webhook_signature,
        handle_webhook_event,
    )
except ImportError:
    from orchestration.billing import (
        can_generate,
        get_entitlement,
        PLANS,
        RazorpayError,
        create_subscription_session,
        cancel_subscription,
        verify_webhook_signature,
        handle_webhook_event,
    )

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=FRONTEND_ORIGIN != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

database_service = DatabaseService()
skillmatcher = SkillsMatcher()

RESUME_SERVICE_URL = os.getenv("RESUME_SERVICE_URL", "http://127.0.0.1:8001")
GENERATE_RESUME_SERVICE_URL = os.getenv(
    "GENERATE_RESUME_SERVICE_URL", "http://127.0.0.1:8003"
)
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "")

# Master switch for paid checkout (Razorpay). Only "true" enables subscriptions.
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "").lower() == "true"

# How long a `pending` generation record may sit before being swept to `failed`
# when the worker never calls back.
GENERATION_TIMEOUT_MIN = 15


class GenerateResumeRequest(BaseModel):
    email: str
    job_id: str


class GenerateResumeCallback(BaseModel):
    record_id: str
    status: str
    url: Optional[str] = None
    updated_resume: Optional[dict] = None
    error: Optional[str] = None


@app.get("/")
def health():
    return {"message": "Orchestration service is up and running!"}


@app.post("/checkUser")
async def check_user_exists(email: str = Body(..., embed=True)):
    try:
        user = database_service.find({"email": email}, "resumeData")
        return {"response": True if user else False}
    except Exception as err:
        logger.error(f"No user with {email} present")
        raise HTTPException(500, "Failure while fetching the user")


@app.post("/uploadResume")
async def upload_resume(file: UploadFile, email: str = Form(...)):
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{RESUME_SERVICE_URL}/upload",
                data={"email": email},
                files={
                    "file": (
                        file.filename,
                        file.file,
                        file.content_type,
                    )
                },
            )

        if response.status_code >= 400:
            try:
                error = response.json()
                detail = error.get("detail", "Resume service returned an error.")
            except Exception:
                detail = response.text or "Resume service returned an error."

            logger.error(
                "Resume service failed | Status=%s | Detail=%s",
                response.status_code,
                detail,
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=detail,
            )

        parsed_response = response.json()
        try:
            database_service.insert(
                {
                    "email": email,
                    "resume_data": parsed_response,
                },
                "resumeData",
                {"email": email},
            )
        except Exception:
            logger.exception("Failed to insert resume into MongoDB.")
            raise HTTPException(
                status_code=500,
                detail="Unable to save the parsed resume.",
            )
        return {"message": "Resume uploaded successfully."}

    except httpx.ConnectError:
        logger.exception("Unable to connect to Resume Service.")
        raise HTTPException(
            status_code=503,
            detail="Resume service is currently unavailable.",
        )
    except httpx.ReadTimeout:
        logger.exception("Resume service timed out.")
        raise HTTPException(
            status_code=504,
            detail="Resume service took too long to respond.",
        )
    except httpx.TimeoutException:
        logger.exception("Request to Resume Service timed out.")
        raise HTTPException(
            status_code=504,
            detail="Request timed out while contacting the resume service.",
        )
    except httpx.RequestError as e:
        logger.exception("HTTP request failed: %s", str(e))
        raise HTTPException(
            status_code=502,
            detail="Unable to communicate with the resume service.",
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error while uploading resume.")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing the resume.",
        )


@app.post("/jobMatch")
async def job_match(email: str = Body(..., embed=True)):
    try:
        if not verify_correct_email_format(email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        user_data = database_service.find({"email": email}, "resumeData")
        if not user_data:
            raise HTTPException(status_code=404, detail="No resume found for user.")

        existing_count = database_service.count({"email": email}, "matches")
        if existing_count == 0:
            resume_data = user_data["resume_data"]["parsed_resume"]
            jobs = database_service.find_many({}, "jobData")
            job_matches = []
            for job in jobs:
                score = score_resume(resume_data, job, skillmatcher)
                if score > 50:
                    job_matches.append({"job_id": str(job["_id"]), "score": score})
            database_service.bulk_upsert_matches(email, job_matches)
        else:
            matches = database_service.find_many(
                {"email": email},
                "matches",
                projection={"job_id": 1, "score": 1},
                sort=[("score", -1)],
            )
            job_matches = [
                {"job_id": m["job_id"], "score": m.get("score", 0)} for m in matches
            ]
        return {"jobs": job_matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail="Job matching failed.")


@app.get("/jobs")
async def get_jobs(
    email: str,
    page: int = 1,
    page_size: int = 20,
):
    try:
        if not verify_correct_email_format(email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        if page < 1:
            page = 1
        page_size = max(1, min(page_size, 100))

        # Matches live in their own collection ({email, job_id, score}), so this
        # never touches the multi-MB `matches` array embedded in resumeData.
        total = database_service.count({"email": email}, "matches")
        if total == 0:
            # No matches persisted yet -> the frontend triggers /jobMatch and retries.
            raise HTTPException(status_code=404, detail="No matches found for user.")

        start = (page - 1) * page_size
        matches = database_service.find_many(
            {"email": email},
            "matches",
            projection={"job_id": 1, "score": 1},
            sort=[("score", -1)],
            skip=start,
            limit=page_size,
        )
        has_more = start + len(matches) < total

        resolved = []
        if matches:
            object_ids = [
                ObjectId(m["job_id"])
                for m in matches
                if ObjectId.is_valid(m.get("job_id", ""))
            ]
            jobs = (
                database_service.find_many(
                    {"_id": {"$in": object_ids}},
                    "jobData",
                    {"raw_description": 0},
                )
                if object_ids
                else []
            )
            jobs_by_key = {str(job["_id"]): job for job in jobs}

            for m in matches:
                job = jobs_by_key.get(str(m["job_id"]))
                if job is None:
                    continue
                job["_id"] = str(job["_id"])
                if isinstance(job.get("posted_at"), datetime):
                    job["posted_at"] = job["posted_at"].isoformat()
                job["score"] = m.get("score", 0)
                resolved.append(job)

        return {
            "jobs": resolved,
            "meta": {"total": total},
            "total": total,
            "has_more": has_more,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail="Could not load jobs.")


@app.post("/generateResume")
async def generate_resume(request: GenerateResumeRequest):
    try:
        email = request.email
        job_id = request.job_id

        user_data = database_service.find({"email": email}, "resumeData")
        if not user_data:
            raise HTTPException(status_code=404, detail="No resume found for user.")

        usage = can_generate(database_service, email)
        if not usage.get("allowed", False):
            raise HTTPException(
                status_code=402,
                detail={"usage": usage},
            )

        job_data = database_service.find({"job_id": job_id}, "jobData")
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found.")

        parsed_resume = user_data["resume_data"]["parsed_resume"]
        job_title = job_data.get("title") or job_data.get("company")

        now = datetime.utcnow().isoformat()
        record_id = database_service.create(
            {
                "email": email,
                "job_id": job_id,
                "status": "pending",
                "url": None,
                "job_title": job_title,
                "created_at": now,
                "updated_at": now,
            },
            "generatedResumes",
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{GENERATE_RESUME_SERVICE_URL}/generate",
                    json={
                        "record_id": str(record_id),
                        "email": email,
                        "job_id": job_id,
                        "resume_data": parsed_resume,
                        "job_description": job_data["raw_description"],
                    },
                )
        except httpx.TimeoutException:
            logger.warning(
                "generateResume is still processing record %s; worker will call back.",
                record_id,
            )
            return {
                "triggered": True,
                "id": str(record_id),
                "status": "pending",
            }
        except Exception:
            logger.exception("Failed to trigger resume generation.")
            database_service.update(
                {"_id": ObjectId(record_id)},
                "generatedResumes",
                {"status": "failed", "updated_at": datetime.utcnow().isoformat()},
            )
            raise HTTPException(
                status_code=502,
                detail="Unable to trigger resume generation.",
            )

        if resp.status_code >= 400:
            database_service.update(
                {"_id": ObjectId(record_id)},
                "generatedResumes",
                {"status": "failed", "updated_at": datetime.utcnow().isoformat()},
            )
            raise HTTPException(
                status_code=resp.status_code,
                detail="Resume generation service failed to trigger the worker.",
            )

        return {"triggered": True, "id": str(record_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generateResume/callback")
async def generate_resume_callback(request: Request, body: GenerateResumeCallback):
    if CALLBACK_SECRET and request.headers.get("x-callback-secret") != CALLBACK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid callback secret.")

    record = database_service.find({"_id": ObjectId(body.record_id)}, "generatedResumes")
    if not record:
        raise HTTPException(status_code=404, detail="Generation record not found.")

    updates = {
        "status": body.status,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if body.url:
        updates["url"] = body.url
    if body.updated_resume is not None:
        updates["updated_resume"] = body.updated_resume
    if body.error:
        updates["error"] = body.error
    database_service.update(
        {"_id": ObjectId(body.record_id)}, "generatedResumes", updates
    )

    push_to_user(
        record["email"],
        {
            "event": "resume_generated",
            "record_id": body.record_id,
            "status": body.status,
            "url": body.url,
            "error": body.error,
        },
    )
    return {"ok": True}


@app.get("/generatedResumes")
def list_generated_resumes(email: str):
    cutoff = (datetime.utcnow() - timedelta(minutes=GENERATION_TIMEOUT_MIN)).isoformat()
    database_service.update_many(
        {"email": email, "status": "pending", "created_at": {"$lt": cutoff}},
        "generatedResumes",
        {
            "status": "failed",
            "updated_at": datetime.utcnow().isoformat(),
            "error": "Generation timed out",
        },
    )
    records = database_service.find_many({"email": email}, "generatedResumes")
    for record in records:
        record["_id"] = str(record["_id"])
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"resumes": records}


@app.get("/usage")
def get_usage(email: str):
    try:
        if not verify_correct_email_format(email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        return get_entitlement(database_service, email)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail="Could not load usage.")


@app.get("/plans")
def list_plans():
    try:
        plans = [dict(plan) for plan in PLANS.values()]
        return {"plans": plans}
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail="Could not load plans.")


@app.post("/subscription/create")
async def create_subscription(email: str = Body(...), plan: str = Body(...)):
    try:
        if not PAYMENTS_ENABLED:
            raise HTTPException(
                status_code=503,
                detail={"message": "Paid plans are not available right now."},
            )
        if not verify_correct_email_format(email):
            raise HTTPException(status_code=400, detail={"message": "Invalid email format"})
        checkout = await create_subscription_session(database_service, email, plan)
        key_id = os.getenv("RAZORPAY_KEY_ID", "")
        if not key_id:
            raise HTTPException(
                status_code=500, detail={"message": "Razorpay is not configured."}
            )
        return {"checkout": checkout, "key_id": key_id}
    except RazorpayError as e:
        logger.warning("Subscription create failed: %s", e)
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(
            status_code=500, detail={"message": "Could not start checkout."}
        )


@app.post("/subscription/cancel")
async def cancel_subscription_endpoint(email: str = Body(..., embed=True)):
    try:
        if not verify_correct_email_format(email):
            raise HTTPException(status_code=400, detail={"message": "Invalid email format"})
        return await cancel_subscription(database_service, email)
    except RazorpayError as e:
        logger.warning("Subscription cancel failed: %s", e)
        raise HTTPException(status_code=400, detail={"message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(
            status_code=500, detail={"message": "Could not cancel subscription."}
        )


@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    try:
        body_bytes = await request.body()
        signature = request.headers.get("X-Razorpay-Signature")
        if not verify_webhook_signature(body_bytes, signature):
            raise HTTPException(status_code=401, detail="Invalid signature.")
        event = json.loads(body_bytes.decode("utf-8"))
        return handle_webhook_event(database_service, event)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
        raise HTTPException(status_code=500, detail="Webhook processing failed.")


if Mangum is not None:
    handler = Mangum(app)


def lambda_handler(event: dict, context) -> dict:
    if event.get("requestContext", {}).get("eventType") in {
        "CONNECT",
        "MESSAGE",
        "DISCONNECT",
    }:
        return handle_ws_event(event, context)
    return handler(event, context)
