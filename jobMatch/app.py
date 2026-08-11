from fastapi import FastAPI, HTTPException
try:
    from mangum import Mangum
except ImportError:  # non-Lambda platforms (e.g. Vercel) run FastAPI's ASGI `app` directly
    Mangum = None
from pydantic import BaseModel, Field
from common.models import ResumeData
from common.helpers import verify_correct_email_format
from common.skills import SkillsMatcher
from common.scoring import score_resume

skillmatcher = SkillsMatcher()
app = FastAPI()

class FindMatchesRequest(BaseModel):
    email: str
    resumeData: ResumeData
    jobs: list = Field(default_factory=list)

@app.get("/")
def health_check():
    return {"message" : "Job Match Service is up!"}

@app.post("/findMatches")
def find_matches(findMatchesRequest: FindMatchesRequest):
    try:
        email = findMatchesRequest.email
        resumeData = findMatchesRequest.resumeData
        if not verify_correct_email_format(email):
            raise HTTPException(400, "Invalid email format")
        resume_data = resumeData.model_dump(mode="json")
        filtered_list = []
        for job in findMatchesRequest.jobs:
            if score_resume(resume_data, job, skillmatcher) > 50:
                job["_id"] = str(job["_id"])
                filtered_list.append(job)
        return {"filtered_list" : filtered_list}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(501, str(e))

if Mangum is not None:
    handler = Mangum(app)
