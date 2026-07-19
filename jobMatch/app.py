from fastapi import FastAPI,HTTPException,Form
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from resumeUpload.ResumeDataParser import ResumeData
from calculate_skills_score import SkillsMatcher
from resumeUpload.helpers import verify_correct_email_format
import math
from pydantic import BaseModel
load_dotenv()
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
client = MongoClient(
    f"mongodb+srv://{MONGO_USERNAME}:{MONGO_PASSWORD}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
)
db = client['jobHunter']
jobs_collection = db['jobData']
users_collection = db['resumeData']
skillmatcher = SkillsMatcher()
app = FastAPI()

class FindMatchesRequest(BaseModel):
    email: str
    resumeData: ResumeData

def score_resume(resumeData: ResumeData, job) -> int:
    try:
        yoe_required_by_job = job.get('extracted').get('required_experience_years')
        if yoe_required_by_job is not None and resumeData.total_experience_months >= yoe_required_by_job * 12:
            years_score = 1
        else:
            years_score = 0
        user_skills = resumeData.skills
        required_skills = job.get('extracted').get('required_skills')
        nice_to_have_skills = job.get('extracted').get('nice_to_have_skills')
        if user_skills is None or required_skills is None:
            return 0
        skills_score = skillmatcher.calculate_skills_score(user_skills,required_skills,nice_to_have_skills)['skills_score']
        return math.ceil(50 * years_score + 50 * skills_score)
    except Exception as e:
        return 0
    
@app.get("/")
def health_check():
    return {"message" : "Job Match Service is up!"}

@app.post("/findMatches")
def find_matches(findMatchesRequest: FindMatchesRequest):
    try:
        email = findMatchesRequest.email
        resumeData = findMatchesRequest.resumeData
        print(email," -> ",resumeData)
        if verify_correct_email_format(email) is False:
            return HTTPException(400, "Invalid email format")
        jobs = jobs_collection.find({}).to_list()
        filtered_list = []
        for job in jobs:
            if score_resume(resumeData,job) > 50:
                job["_id"] = str(job["_id"])
                filtered_list.append(job)
        users_collection.update_one({"email": email}, {"$set":{"matches":filtered_list}})
        return {"filtered_list" : filtered_list}
    except PyMongoError as err:
        return HTTPException(502,f"Unable to update the user jobs matches {str(err)}")
    except Exception as e:
        return HTTPException(501, str(e))
    