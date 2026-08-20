from fastapi import FastAPI, HTTPException
try:
    from mangum import Mangum
except ImportError:  # non-Lambda platforms (e.g. Vercel) run FastAPI's ASGI `app` directly
    Mangum = None
import boto3
import json
import os
import requests
from dotenv import load_dotenv
from pydantic import BaseModel
from requests_aws4auth import AWS4Auth
from common.models import ResumeData
load_dotenv()

app = FastAPI()

LAMBDA_FUNCTION_NAME = os.getenv("LAMBDA_FUNCTION_NAME", "generateResumeWorker")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
WORKER_FUNCTION_URL = os.getenv("WORKER_FUNCTION_URL", "").rstrip("/")


def _aws4_auth() -> AWS4Auth:
    creds = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
    ).get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials available to sign the worker request.")
    frozen = creds.get_frozen_credentials()
    return AWS4Auth(
        frozen.access_key,
        frozen.secret_key,
        AWS_REGION,
        "lambda",
        session_token=frozen.token,
    )


class GenerateRequest(BaseModel):
    record_id: str
    email: str
    job_id: str
    resume_data: dict
    job_description: str


@app.get("/")
def health():
    return {"message": "Resume Generate service is up!!"}


@app.post("/generate")
def generate_resume(request: GenerateRequest):
    payload = {
        "record_id": request.record_id,
        "email": request.email,
        "job_id": request.job_id,
        "resume_data": request.resume_data,
        "job_description": request.job_description,
    }
    try:
        if WORKER_FUNCTION_URL:
            response = requests.post(
                f"{WORKER_FUNCTION_URL}/",
                json=payload,
                auth=_aws4_auth(),
                timeout=320,
            )
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Worker returned {response.status_code}: {response.text[:500]}",
                )
        else:
            lambda_client = boto3.client("lambda", region_name=AWS_REGION)
            lambda_client.invoke(
                FunctionName=LAMBDA_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps(payload),
            )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to trigger resume generation. {err}",
        )
    return {"triggered": True}


if Mangum is not None:
    handler = Mangum(app)
