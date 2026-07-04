from fastapi import FastAPI,UploadFile,HTTPException,Form
from ResumeDataParser import ResumeDataParser
from ResumeService import ResumeService
from pymongo import MongoClient
import os
from dotenv import load_dotenv
from helpers import verify_correct_email_format, ResumeParsingException
load_dotenv()
username = os.getenv("MONGO_USERNAME")
password = os.getenv("MONGO_PASSWORD")

client = MongoClient(f"mongodb+srv://{username}:{password}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0")
parser = ResumeDataParser()
resume_service = ResumeService()

app = FastAPI()
@app.get("/")
def entry():
    return {"message" : "You have arrived at my project, Welcome !"}

@app.post("/upload")
async def resume_upload(file: UploadFile,email: str = Form(...)):
    print(f"Received file: {file.filename}, Content-Type: {file.content_type}, Email: {email}")
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Only PDF files are accepted")
    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 5MB)")
    if verify_correct_email_format(email) is False:
        raise HTTPException(400, "Invalid email format")
    try:
        resume_text = resume_service.get_text_from_pdf(file_bytes)
        if not resume_text.strip():
            print(f"Extracted text length: {resume_text.strip()}")
            raise ResumeParsingException(422, "Unable to extract text from the PDF. Please ensure the PDF is not a scanned image and contains selectable text.")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ResumeParsingException as e:
        raise HTTPException(e.args[0], e.args[1])
    except Exception as e:
        # log e with your traceability callback from before
        raise HTTPException(502, "Failed to process resume, please try again")
    try:
        parsed_resume = resume_service.parse_resume(parser,resume_text)
    except Exception as e:
        # log e with your traceability callback from before
        raise HTTPException(422, "Failed to parse resume, please try again")
    try:
        resume_id = resume_service.save_to_mongodb(client,email,parsed_resume)
    except Exception as e:
        # log e with your traceability callback from before
        raise HTTPException(502, "Failed to save resume data, please try again")
    return {"message": "Resume uploaded successfully", "id": resume_id}
