import datetime
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

from cronjob.Jobprocessor import JobPreprocessor
from cronjob.fetch_jobs import (
    generate_unique_id,
    jobs_collection,
    insert_job_into_users_match,
    clean_up_old_job_postings,
)

load_dotenv()
logger = logging.getLogger(__name__)

API_URL = os.getenv("NETWORK_API_URL")
USER_ID = os.getenv("NETWORK_USER_ID") 

LOCATIONS = ['anywhere-in-india','anywhere-in-usa','anywhere-in-canada','anywhere-in-uk','anywhere-in-australia','anywhere-in-singapore','anywhere-in-germany','anywhere-in-france','anywhere-in-netherlands','anywhere-in-sweden','anywhere-in-switzerland','anywhere-in-norway','anywhere-in-denmark','anywhere-in-finland','anywhere-in-ireland','anywhere-in-new-zealand']
MAX_PAGES = int(os.getenv("NETWORK_MAX_PAGES") or "20")

SENIORITY_MAP = {
    "ENTRY": "junior",
    "INTERN": "intern",
    "MID": "mid",
    "SENIOR": "senior",
    "LEAD": "lead",
    "MANAGER": "lead",
    "EXEC": "lead",
    "VP": "lead",
    "MANAGER": "lead",
}

TAXONOMY_LIST = [
    {"taxonomyId": "01-01-01", "title": "Backend Engineer"},
    {"taxonomyId": "01-01-02", "title": "Full Stack Engineer"},
    {"taxonomyId": "01-01-03", "title": "Python Engineer"},
    {"taxonomyId": "01-01-04", "title": "Java Engineer"},
    {"taxonomyId": "01-01-05", "title": "C/C++ Engineer"},
    {"taxonomyId": "01-01-06", "title": ".Net Engineer"},
    {"taxonomyId": "01-01-07", "title": "Golang Engineer"},
    {"taxonomyId": "01-01-08", "title": "Salesforce Developer"},
    {"taxonomyId": "01-01-09", "title": "Blockchain Engineer"},
    {"taxonomyId": "01-02-01", "title": "Frontend Engineer"},
    {"taxonomyId": "01-02-02", "title": "React Developer"},
    {"taxonomyId": "01-02-03", "title": "Angular/Vue Developer"},
    {"taxonomyId": "01-02-04", "title": "UI/UX Developer"},
    {"taxonomyId": "01-03-01", "title": "Android Developer"},
    {"taxonomyId": "01-03-02", "title": "iOS Developer"},
    {"taxonomyId": "01-03-03", "title": "Flutter Developer"},
    {"taxonomyId": "01-03-04", "title": "React Native Developer"},
    {"taxonomyId": "01-04-01", "title": "Game Developer"},
    {"taxonomyId": "01-04-02", "title": "Unity Developer"},
    {"taxonomyId": "01-04-03", "title": "Unreal Engine Developer"},
    {"taxonomyId": "01-04-04", "title": "AR/VR Developer"},
    {"taxonomyId": "01-05-01", "title": "Data Analyst"},
    {"taxonomyId": "01-05-02", "title": "Data Scientist"},
    {"taxonomyId": "01-05-03", "title": "Data Engineer"},
    {"taxonomyId": "01-05-04", "title": "Business/BI Analyst"},
    {"taxonomyId": "01-05-05", "title": "Power BI Developer"},
    {"taxonomyId": "01-05-06", "title": "ETL Developer"},
    {"taxonomyId": "01-05-07", "title": "Data Warehouse Engineer"},
    {"taxonomyId": "01-06-01", "title": "Machine Learning Engineer"},
    {"taxonomyId": "01-06-02", "title": "AI Engineer"},
    {"taxonomyId": "01-06-03", "title": "LLM Engineer"},
    {"taxonomyId": "01-06-04", "title": "ML/AI Researcher"},
    {"taxonomyId": "01-06-05", "title": "Deep Learning Engineer"},
    {"taxonomyId": "01-06-06", "title": "MLOps Engineer"},
    {"taxonomyId": "01-06-07", "title": "Computer Vision Engineer"},
    {"taxonomyId": "01-06-08", "title": "NLP Engineer"},
    {"taxonomyId": "01-06-09", "title": "ML Infrastructure Engineer"},
    {"taxonomyId": "01-06-10", "title": "Data Annotation/AI Tutor"},
    {"taxonomyId": "01-07-01", "title": "DevOps Engineer"},
    {"taxonomyId": "01-07-02", "title": "Site Reliability Engineer (SRE)"},
    {"taxonomyId": "01-07-03", "title": "Platform Engineer"},
    {"taxonomyId": "01-07-04", "title": "Cloud Engineer"},
    {"taxonomyId": "01-07-05", "title": "Systems Engineer"},
    {"taxonomyId": "01-08-01", "title": "Cyber Security Engineer"},
    {"taxonomyId": "01-08-02", "title": "Cyber Security Analyst"},
    {"taxonomyId": "01-08-03", "title": "Cloud Security Engineer"},
    {"taxonomyId": "01-08-04", "title": "Network Security Engineer"},
    {"taxonomyId": "01-08-05", "title": "SOC Analyst"},
    {"taxonomyId": "01-08-06", "title": "Penetration Tester"},
    {"taxonomyId": "01-09-01", "title": "QA/Test Engineer"},
    {"taxonomyId": "01-09-02", "title": "Automation Test Engineer"},
    {"taxonomyId": "01-09-03", "title": "QA Manager"},
    {"taxonomyId": "01-10-01", "title": "IT Support Specialist"},
    {"taxonomyId": "01-10-02", "title": "Help Desk Technician"},
    {"taxonomyId": "01-10-03", "title": "System Administrator"},
    {"taxonomyId": "01-10-04", "title": "Database Administrator"},
    {"taxonomyId": "01-10-05", "title": "Network Engineer"},
    {"taxonomyId": "01-10-06", "title": "Salesforce Administrator"},
    {"taxonomyId": "01-11-01", "title": "Engineering Manager"},
    {"taxonomyId": "01-11-02", "title": "Software Architect"},
    {"taxonomyId": "01-11-03", "title": "Engineering Director/VP"},
    {"taxonomyId": "01-11-04", "title": "CTO"},
    {"taxonomyId": "01-12-01", "title": "Project/Program Manager"},
    {"taxonomyId": "01-12-02", "title": "Technical Project Manager"},
    {"taxonomyId": "01-12-03", "title": "Scrum Master"},
    {"taxonomyId": "01-13-01", "title": "Solutions Architect"},
    {"taxonomyId": "01-13-02", "title": "Sales Engineer"},
    {"taxonomyId": "01-13-03", "title": "Technical Account Manager"},
    {"taxonomyId": "01-13-04", "title": "Developer Relations"},
    {"taxonomyId": "01-13-05", "title": "Technical Writer"},
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(text: str) -> str:
    """Strip HTML tags/entities from a job description."""
    if not text:
        return ""
    import html as html_module

    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def fetch_page(user_id: str, taxonomy_list: list, location: str, page: int) -> dict:
    payload = {
        "userId": user_id,
        "jobTaxonomyList": taxonomy_list,
        "location": location,
        "seniority_level": "",
        "entryL": True,
        "page": page,
    }
    resp = requests.post(API_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def normalize_listing(listing: dict) -> dict:
    """Map a Network API listing to the canonical JSearch-style job shape."""
    ts = listing.get("posted_at_ts")
    posted_at = (
        datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
        if ts
        else None
    )
    location = (listing.get("location") or "").strip()
    country = (listing.get("country") or "").strip()
    location = f"{location}, {country}" if country else location

    return {
        # JSearch-shaped keys so JobPreprocessor + Unicode stack treat it identically
        "job_id": listing.get("id"),
        "job_title": listing.get("role"),
        "employer_name": listing.get("company"),
        "job_apply_link": listing.get("job_url"),
        "job_posted_at_datetime_utc": posted_at,
        "job_location": location,
        "job_is_remote": False,
        "job_publisher": listing.get("source", "network"),
        "job_description": clean_html(listing.get("description", "")),
        "job_employment_type": "Full-time",
        # Structured hints used to override regex-based extraction (additive).
        "_skills": listing.get("skills") or [],
        "_experience_years": listing.get("experience_min") or 0,
        "_seniority_key": listing.get("seniority_key") or "",
        "_source_match_score": listing.get("match_score"),
        "_salary_min": listing.get("salary_min"),
        "_salary_max": listing.get("salary_max"),
    }


def insert_listing(listing: dict) -> bool:
    try:
        job = normalize_listing(listing)
        job_id = job.get("job_id")
        if not job_id:
            logger.warning("Skipping listing with no id")
            return False

        job["_id"] = generate_unique_id(job_id)

        preprocessor = JobPreprocessor()
        processed_job = preprocessor.preprocess_job(job)

        # Prefer the API's structured fields over regex inference.
        extracted = processed_job["extracted"]
        skills = job["_skills"]
        if skills:
            extracted["required_skills"] = [s.lower() for s in skills]
        if job["_experience_years"]:
            extracted["required_experience_years"] = int(job["_experience_years"])
        seniority_key = job["_seniority_key"]
        if seniority_key:
            extracted["seniority_level"] = SENIORITY_MAP.get(
                seniority_key, extracted["seniority_level"]
            )

        # Additive extras (ignored by existing consumers).
        processed_job["source_match_score"] = job["_source_match_score"]
        processed_job["salary_min"] = job["_salary_min"]
        processed_job["salary_max"] = job["_salary_max"]

        result = jobs_collection.update_one(
            {"job_id": job_id}, {"$set": processed_job}, upsert=True
        )
        insert_job_into_users_match(processed_job)
        if result.upserted_id:
            logger.info(f"Inserted job: {processed_job.get('title')} at {processed_job.get('company')}")
        return True
    except Exception as err:
        logger.error(f"Error inserting listing {listing.get('id')}: {err}")
        return False


def main():
    logger.info("=== Starting Network Job Aggregation ===")
    st_total = time.perf_counter()
    total = 0
    for location in LOCATIONS:
        page = 1
        while page <= MAX_PAGES:
            st = time.perf_counter()
            try:
                data = fetch_page(USER_ID, TAXONOMY_LIST, location, page)
            except Exception as err:
                logger.error(f"Request {location} page {page} failed: {err}")
                break
            listings = data.get("listings") or []
            if not listings:
                logger.info(f"No more listings for {location} at page {page}.")
                break
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(insert_listing, listings))
            total += len(listings)
            logger.info(
                f"{location} page {page}: {len(listings)} jobs in {time.perf_counter() - st:.2f}s"
            )
            if not data.get("has_more"):
                break
            page += 1
    logger.info("=== Network Job Aggregation Complete ===")
    logger.info(f"Total jobs fetched: {total} in {time.perf_counter() - st_total:.2f}s")


if __name__ == "__main__":
    try:
        clean_up_old_job_postings(30)
    except Exception as err:
        logger.error(f"Failed in cleaning up old job postings: {err}")
    main()