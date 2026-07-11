import requests
from dotenv import load_dotenv
import os
from locations import roles, locations
from pymongo import MongoClient
import hashlib
import datetime
import logging
import time
from Jobprocessor import JobPreprocessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

API_URL = os.getenv("API_URL")
API_KEY_ACC1 = os.getenv("API_KEY_ACC1")
API_KEY_ACC2 = os.getenv("API_KEY_ACC2")
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")

client = MongoClient(
    f"mongodb+srv://{MONGO_USERNAME}:{MONGO_PASSWORD}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
)
db = client['jobHunter']
jobs_collection = db['jobData']


def fetch_jobs_from_api(api_key, querystring):
    """Fetch jobs from JSearch API"""
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "jsearch.p.rapidapi.com",
        "Content-Type": "application/json"
    }

    response = requests.get(API_URL, headers=headers, params=querystring)
    return response.json()


def generate_unique_id(job_id):
    """Generate unique ID for job entry"""
    current_date_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    unique_string = f"{job_id}_{current_date_time}"
    unique_hash = hashlib.sha256(unique_string.encode("utf-8")).hexdigest()
    return unique_hash


def insert_jobs_into_mongodb(job):
    """
    Extract job data and insert into MongoDB.
    """
    try:
        job_id = job.get("job_id")
        if not job_id:
            logger.warning("Skipping job with no job_id")
            return False

        # Create unique hash
        unique_hash = generate_unique_id(job_id)
        job["_id"] = unique_hash

        # Preprocess the job (extract skills, experience, etc.)
        preprocessor = JobPreprocessor()
        processed_job = preprocessor.preprocess_job(job)

        # Insert into MongoDB (upsert to handle duplicates)
        result = jobs_collection.update_one(
            {"job_id": job_id},
            {"$set": processed_job},
            upsert=True
        )

        if result.upserted_id:
            logger.info(f"✓ Inserted job: {job.get('job_title')} at {job.get('employer_name')}")
        else:
            logger.debug(f"Updated existing job: {job_id}")

        return True

    except Exception as e:
        logger.error(f"Error inserting job {job.get('job_id')}: {e}")
        return False


def main():
    """Main execution"""
    logger.info("=== Starting Job Aggregation ===")

    # Build queries
    queries = []
    roles_limited = roles[:-12]  # Limit the number of roles

    for country, cities in locations.items():
        if country != "in":  # Only India
            continue
        for city in cities:
            if city not in ["bangalore", "hyderabad", "pune", "mumbai"]:
                continue
            for role in roles_limited:
                queries.append({
                    "query": f"{role} jobs in {city}",
                    "num_pages": 10,
                    "country": country,
                    "date_posted": "all",
                })

    logger.info(f"Total queries to process: {len(queries[:10])}")

    # Process queries
    st_t = time.perf_counter()
    total_jobs_fetched = 0
    total_jobs_inserted = 0

    for i, query in enumerate(queries[:10]):
        st = time.perf_counter()

        # Alternate between API keys
        api_key = API_KEY_ACC1 if i % 2 == 0 else API_KEY_ACC2

        try:
            jobs_data = fetch_jobs_from_api(api_key, query)
            en = time.perf_counter()
            logger.info(f"Request {i} took {en - st:.2f}s")

            if jobs_data.get("status", "").lower().strip() == "ok":
                jobs_list = jobs_data.get("data", {}).get("jobs", [])
                total_jobs_fetched += len(jobs_list)

                for job in jobs_list:
                    if insert_jobs_into_mongodb(job):
                        total_jobs_inserted += 1

            else:
                logger.warning(f"Query {i} returned status: {jobs_data.get('status')}")

        except Exception as e:
            logger.error(f"Error processing query {i}: {e}")
            continue

    en_t = time.perf_counter()

    logger.info("=== Job Aggregation Complete ===")
    logger.info(f"Total jobs fetched: {total_jobs_fetched}")
    logger.info(f"Total jobs inserted/updated: {total_jobs_inserted}")
    logger.info(f"Total time taken: {en_t - st_t:.2f}s")


if __name__ == "__main__":
    main()