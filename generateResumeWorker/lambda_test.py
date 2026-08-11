import boto3
import requests
from requests_aws4auth import AWS4Auth
import os
from dotenv import load_dotenv
load_dotenv()
REGION = "us-east-1"
FUNCTION_URL = "https://kup4ccnttbuz76jt3yml6h5la40drpxs.lambda-url.us-east-1.on.aws/"

session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)
print(session.get_credentials().access_key)

credentials = session.get_credentials().get_frozen_credentials()

auth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    REGION,
    "lambda",
    session_token=credentials.token,
)

response = requests.get(FUNCTION_URL, auth=auth)

print("Status:", response.status_code)
print("Body:", response.text)