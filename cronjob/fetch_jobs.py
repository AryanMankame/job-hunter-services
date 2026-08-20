import requests
from dotenv import load_dotenv
import os
from cronjob.locations import roles, locations
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import hashlib
import datetime
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from cronjob.Jobprocessor import JobPreprocessor
from common.skills import SkillsMatcher
from common.scoring import score_resume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

API_URL = os.getenv("API_URL")
API_KEY_ACC1 = os.getenv("API_KEY_ACC1")
API_KEY_ACC2 = os.getenv("API_KEY_ACC2")
API_KEY_ACC3 = os.getenv("API_KEY_ACC3")
API_KEY_ACC4 = os.getenv("API_KEY_ACC4")
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")

client = MongoClient(
    f"mongodb+srv://{MONGO_USERNAME}:{MONGO_PASSWORD}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
)
db = client['jobHunter']
jobs_collection = db['jobData']
users_collection = db['resumeData']
matches_collection = db['matches']
skillmatcher = SkillsMatcher()

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
        insert_job_into_users_match(processed_job)
        if result.upserted_id:
            logger.info(f"✓ Inserted job: {job.get('job_title')} at {job.get('employer_name')}")
        else:
            logger.debug(f"Updated existing job: {job_id}")

        return True

    except Exception as e:
        logger.error(f"Error inserting job {job.get('job_id')}: {e}")
        return False

def clean_up_old_job_postings(days: int = 30) -> int:
    """
    Delete job postings older than `days` and remove their references
    from the matches collection.

    Matches reference a job by its `jobData._id` string (`job_id` field in the
    matches collection). Some legacy references may still hold the network
    `job_id` (jobData `job_id` field), so we remove by both forms.
    """
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    old_jobs = list(
        jobs_collection.find(
            {"preprocessed_at": {"$lt": cutoff}},
            {"job_id": 1, "_id": 1},
        )
    )
    if not old_jobs:
        logger.info("No expired job postings found.")
        return 0
    job_id_fields = [job["job_id"] for job in old_jobs if job.get("job_id")]
    ref_keys = list(job_id_fields) + [str(job["_id"]) for job in old_jobs]
    with client.start_session() as session:
        with session.start_transaction():
            delete_result = jobs_collection.delete_many(
                {"job_id": {"$in": job_id_fields}},
                session=session,
            )
            matches_collection.delete_many(
                {"job_id": {"$in": ref_keys}},
                session=session,
            )
    logger.info(f"Removed {delete_result.deleted_count} expired job postings.")
    return delete_result.deleted_count

def insert_job_into_users_match(job):
    """
    Insert job into already existing user's matches collection.
    """
    try:
        job_ref = jobs_collection.find_one({"job_id": job["job_id"]}, {"_id": 1})
        if not job_ref:
            return
        job_data_id = str(job_ref["_id"])
        users = users_collection.find({}).to_list()
        for user in users:
            resume_score = score_resume(user['resume_data']['parsed_resume'], job, skillmatcher)
            if resume_score > 50:
                matches_collection.update_one(
                    {"email": user["email"], "job_id": job_data_id},
                    {"$set": {"email": user["email"], "job_id": job_data_id, "score": resume_score}},
                    upsert=True,
                )
    except PyMongoError as err:
        logger.error(f"Error inserting job into user: {err}")

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

    logger.info(f"Total queries to process: {len(queries)}")

    # Process queries
    st_t = time.perf_counter()
    total_jobs_fetched = 0
    for i, query in enumerate(queries[1:]):
        st = time.perf_counter()

        # Alternate between API keys
        api_key = ''
        match (i%4):
            case 0:
                api_key = API_KEY_ACC1
            case 1:
                api_key = API_KEY_ACC2
            case 2:
                api_key = API_KEY_ACC3
            case 3:
                api_key = API_KEY_ACC4
        try:
            jobs_data = fetch_jobs_from_api(api_key, query)
            en = time.perf_counter()
            logger.info(f"Request {i} took {en - st:.2f}s")

            if jobs_data.get("status", "").lower().strip() == "ok":
                jobs_list = jobs_data.get("data", {}).get("jobs", [])
                total_jobs_fetched += len(jobs_list)

                with ThreadPoolExecutor(max_workers=4) as executor:
                    res = list(executor.map(insert_jobs_into_mongodb,jobs_list))

            else:
                logger.warning(f"Query {i} returned status: {jobs_data.get('status')}")

        except Exception as e:
            logger.error(f"Error processing query {i}: {e}")
            continue

    en_t = time.perf_counter()

    logger.info("=== Job Aggregation Complete ===")
    logger.info(f"Total jobs fetched: {total_jobs_fetched}")
    logger.info(f"Total time taken: {en_t - st_t:.2f}s")


if __name__ == "__main__":
    try:
        clean_up_old_job_postings(30)
    except Exception as e:
        logger.error("Failed in cleaning up old job postings: {e}")
    main()