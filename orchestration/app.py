from fastapi import FastAPI,UploadFile,Form,HTTPException,Body
from DatabaseService import DatabaseService
from resumeUpload.ResumeDataParser import ResumeData
from bson import json_util
import json
import httpx
import logging
logger = logging.getLogger(__name__)

app = FastAPI()

database_service = DatabaseService()

@app.get("/")
def health():
    return {"message" : "Orchestration service is up and running!"}


@app.post("/checkUser")
async def check_user_exists(email: str = Body(...,embed=True)):
    try:
        user = database_service.find({"email" : email},"resumeData")
        return {"response" : True if user else False }
    except Exception as err:
        logging.error(f"No user with {email} present")
        return HTTPException(500,"Failure while fetching the user")


@app.post("/uploadResume")
async def upload_resume(
    file: UploadFile,
    email: str = Form(...)
):
    try:

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "http://127.0.0.1:8001/upload",
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
            inserted_id = database_service.insert({
                "email" : email,
                "resume_data" : parsed_response
            }, "resumeData",{"email" : email})
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
async def job_match(email: str = Body(...,embed=True)):
    # steps:
    # send email from ui -> check if user has a resume data for it, if yes then call the jobMatch service get the matches and save to resumeData
    try:
        user_data = database_service.find({"email" : email},"resumeData")
        id = user_data["_id"]
        job_matches = []
        if 'matches' not in user_data.keys():
            # user_data = ResumeData.model_validate(user_data['resume_data']['parsed_resume'])
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post("http://127.0.0.1:8002/findMatches", json={"email" : email, "resumeData" : user_data['resume_data']['parsed_resume'] })
                if resp.status_code == 200:
                    job_matches = resp.json()
                    user_data['matches'] = job_matches['filtered_list']
                    database_service.insert(user_data,"resumeData",{"_id" : id})
        else:
            job_matches = user_data['matches']
        return { "jobs" : job_matches }
    except Exception as e:
        logger.exception("Unexpected error while processing the request.")
