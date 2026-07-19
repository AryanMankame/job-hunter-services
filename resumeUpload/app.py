from fastapi import FastAPI, UploadFile, HTTPException, Form
from ResumeDataParser import ResumeDataParser
from ResumeService import ResumeService
from pymongo import MongoClient
from helpers import verify_correct_email_format, ResumeParsingException
from dotenv import load_dotenv
import logging
import os

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

username = os.getenv("MONGO_USERNAME")
password = os.getenv("MONGO_PASSWORD")

client = MongoClient(
    f"mongodb+srv://{username}:{password}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
)

parser = ResumeDataParser()
resume_service = ResumeService()
app = FastAPI()

@app.get("/")
def entry():
    return {"message": "Welcome to the Resume Upload Service!"}

@app.post("/upload")
async def resume_upload(
    file: UploadFile,
    email: str = Form(...)
):
    logger.info(
        f"Received upload request | File: {file.filename} | Email: {email}"
    )
    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are supported."
        )
    # Read uploaded file
    file_bytes = await file.read()
    # Validate file size
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="The uploaded PDF exceeds the maximum allowed size of 5 MB."
        )
    # Validate email
    if not verify_correct_email_format(email):
        raise HTTPException(
            status_code=400,
            detail="Please provide a valid email address."
        )
    # Extract text from PDF
    try:
        resume_text = resume_service.get_text_from_pdf(file_bytes)
        if not resume_text.strip():
            raise ResumeParsingException(
                422,
                "Unable to extract text from the PDF. Please upload a text-based PDF instead of a scanned document."
            )
    except ValueError as e:
        logger.exception("PDF extraction failed.")
        raise HTTPException(
            status_code=422,
            detail=str(e)
        )
    except ResumeParsingException as e:
        logger.exception("Resume text extraction failed.")
        raise HTTPException(
            status_code=e.args[0],
            detail=e.args[1]
        )
    except Exception:
        logger.exception("Unexpected error during PDF extraction.")
        raise HTTPException(
            status_code=500,
            detail="Failed to process the uploaded PDF. Please try again later."
        )
    # Parse resume
    try:
        parsed_resume = resume_service.parse_resume(parser, resume_text)
    except Exception:
        logger.exception("Resume parsing failed.")
        raise HTTPException(
            status_code=422,
            detail="Unable to parse the resume. Please ensure the resume contains readable and complete information."
        )
    # Save to MongoDB
    try:
        resume_id = resume_service.save_to_mongodb(
            client,
            email,
            parsed_resume
        )
    except Exception:
        logger.exception("Failed to save resume.")
        raise HTTPException(
            status_code=500,
            detail="Unable to save the resume at the moment. Please try again later."
        )
    return {
        "message": "Resume uploaded successfully.",
        "resume_id": resume_id
    }