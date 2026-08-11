from fastapi import FastAPI, UploadFile, Form, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
try:
    from mangum import Mangum
except ImportError:  # non-Lambda platforms (e.g. Vercel) run FastAPI's ASGI `app` directly
    Mangum = None
from common.database import DatabaseService
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
        PLANS,
        can_generate,
        create_subscription_session,
        get_entitlement,
        handle_webhook_event,
        verify_webhook_signature,
    )
except ImportError:
    from orchestration.billing import (
        PLANS,
        can_generate,
        create_subscription_session,
        get_entitlement,
        handle_webhook_event,
        verify_webhook_signature,
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

RESUME_SERVICE_URL = os.getenv("RESUME_SERVICE_URL", "http://127.0.0.1:8001")
JOB_MATCH_SERVICE_URL = os.getenv("JOB_MATCH_SERVICE_URL", "http://127.0.0.1:8002")
GENERATE_RESUME_SERVICE_URL = os.getenv(
    "GENERATE_RESUME_SERVICE_URL", "http://127.0.0.1:8003"
)
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "")
GENERATION_TIMEOUT_MIN = int(os.getenv("GENERATION_TIMEOUT_MIN", "10"))


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
        user_data = database_service.find({"email": email}, "resumeData")
        if not user_data:
            raise HTTPException(status_code=404, detail="No resume found for user.")
        id = user_data["_id"]
        job_matches = []
        if "matches" not in user_data.keys():
            jobs = database_service.find_many({}, "jobData")
            for job in jobs:
                job["_id"] = str(job["_id"])
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{JOB_MATCH_SERVICE_URL}/findMatches",
                    json={
                        "email": email,
                        "resumeData": user_data["resume_data"]["parsed_resume"],
                        "jobs": jobs,
                    },
                )
                if resp.status_code == 200:
                    job_matches = resp.json()
                    user_data["matches"] = job_matches["filtered_list"]
                    database_service.insert(user_data, "resumeData", {"_id": id})
        else:
            job_matches = user_data["matches"]
        return {"jobs": job_matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")


@app.post("/generateResume")
async def generate_resume(request: GenerateResumeRequest):
    try:
        email = request.email
        job_id = request.job_id

        user_data = database_service.find({"email": email}, "resumeData")
        if not user_data:
            raise HTTPException(status_code=404, detail="No resume found for user.")

        job_data = database_service.find({"job_id": job_id}, "jobData")
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found.")

        usage = can_generate(database_service, email)
        if not usage["allowed"]:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": "You've reached your generation limit. Upgrade your plan to generate more resumes.",
                    "usage": usage,
                },
            )

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
        {
            "email": email,
            "status": "pending",
            "created_at": {"$lt": cutoff},
        },
        "generatedResumes",
        {
            "status": "failed",
            "error": "Generation timed out",
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    records = database_service.find_many({"email": email}, "generatedResumes")
    for record in records:
        record["_id"] = str(record["_id"])
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"resumes": records}


# ─── Subscription & billing endpoints ────────────────────────────────────────


@app.get("/plans")
def list_plans():
    return {"plans": list(PLANS.values())}


@app.get("/usage")
def current_usage(email: str):
    try:
        return get_entitlement(database_service, email)
    except Exception:
        logger.exception("Failed to fetch usage for %s", email)
        raise HTTPException(status_code=500, detail="Failed to fetch usage.")


class CreateSubscriptionRequest(BaseModel):
    email: str
    plan: str


@app.post("/subscription/create")
async def create_subscription(request: CreateSubscriptionRequest):
    try:
        # Prevent double-billing if a paid subscription is still active.
        current = get_entitlement(database_service, request.email)
        if current["plan"] != "free":
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "You already have an active subscription.",
                    "usage": current,
                },
            )
        session = await create_subscription_session(
            database_service, request.email, request.plan
        )
        return {"checkout": session, "key_id": os.getenv("RAZORPAY_KEY_ID")}
    except HTTPException:
        raise
    except Exception as err:
        logger.exception("Failed to create subscription for %s", request.email)
        raise HTTPException(status_code=500, detail=str(err))


@app.post("/subscription/cancel")
async def cancel_subscription(email: str = Body(..., embed=True)):
    try:
        sub = database_service.find({"email": email}, "subscriptions")
        if not sub:
            raise HTTPException(status_code=404, detail="No subscription found.")
        database_service.update(
            {"email": email},
            "subscriptions",
            {"status": "cancelled", "updated_at": datetime.utcnow().isoformat()},
        )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to cancel subscription for %s", email)
        raise HTTPException(status_code=500, detail="Failed to cancel subscription.")


@app.post("/subscription/webhook")
async def subscription_webhook(request: Request):
    body_bytes = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if not verify_webhook_signature(body_bytes, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    try:
        event = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")
    try:
        result = handle_webhook_event(database_service, event)
    except Exception:
        logger.exception("Webhook handling failed.")
        raise HTTPException(status_code=500, detail="Webhook processing failed.")
    return result


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
