from supabase import create_client
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BUCKET = os.environ["SUPABASE_BUCKET"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

local_file = "/Users/aryanmankame/Projects/jobHunter/POC/AryanMankameResume.pdf"  # Put a sample PDF in the same folder
storage_path = "test/resume.pdf"

with open(local_file, "rb") as f:
    response = supabase.storage.from_(BUCKET).upload(
        path=storage_path,
        file=f,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true"
        }
    )

print("Upload Response:")
print(response)

public_url = supabase.storage.from_(BUCKET).get_public_url(storage_path)

print("\nPublic URL:")
print(public_url)