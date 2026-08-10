"""Remove duplicate job entries from user matches and jobData.

Reads creds from cronjob/.env (same as fetch_jobs.py). Default is a dry-run
that only prints what would change; pass --apply to actually write to Mongo.

Usage:
    python cleanup_duplicate_matches.py              # dry-run
    python cleanup_duplicate_matches.py --apply      # commit
"""
import argparse
import logging
import os

from dotenv import load_dotenv
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_URI = (
    f"mongodb+srv://{MONGO_USERNAME}:{MONGO_PASSWORD}"
    "@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
)

client = MongoClient(MONGO_URI)
db = client["jobHunter"]
users_collection = db["resumeData"]
jobs_collection = db["jobData"]


def dedupe_matches(matches: list) -> list:
    """Keep newest entry (by preprocessed_at) per job_id; drop older dupes.

    Entries with no job_id are preserved untouched.
    """
    best: dict[str, dict] = {}
    seq: dict[str, int] = {}
    no_id: list = []

    for match in matches:
        jid = match.get("job_id")
        if jid is None:
            no_id.append(match)
            continue
        key = str(jid)
        if key not in best:
            best[key] = match
            seq[key] = len(seq)
        else:
            current = match.get("preprocessed_at") or ""
            previous = best[key].get("preprocessed_at") or ""
            if current > previous:
                best[key] = match

    result = sorted(best.values(), key=lambda m: seq[str(m.get("job_id"))])
    result.extend(no_id)
    return result


def cleanup_users(apply: bool) -> tuple[int, int, int]:
    """Dedupe matches for every user. Return (users checked, users changed, entries removed)."""
    users = list(users_collection.find({}, {"_id": 1, "matches": 1}))
    users_changed = 0
    entries_removed = 0
    for user in users:
        matches = user.get("matches") or []
        if not matches:
            continue
        cleaned = dedupe_matches(matches)
        removed = len(matches) - len(cleaned)
        if removed <= 0:
            continue
        users_changed += 1
        entries_removed += removed
        logger.info(
            "user=%s matches=%d -> %d (%d removed)",
            user.get("email") or user["_id"],
            len(matches),
            len(cleaned),
            removed,
        )
        if apply:
            users_collection.update_one(
                {"_id": user["_id"]}, {"$set": {"matches": cleaned}}
            )
    return len(users), users_changed, entries_removed


def cleanup_jobs(apply: bool) -> tuple[int, int]:
    """For duplicated job_id in jobData, keep the newest doc, delete the rest."""
    dup_ids = [
        d["_id"]
        for d in jobs_collection.aggregate(
            [
                {"$group": {"_id": "$job_id", "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
            ]
        )
    ]
    docs_deleted = 0
    job_ids_changed = 0
    for job_id in dup_ids:
        to_remove = list(
            jobs_collection.find(
                {"job_id": job_id}, {"preprocessed_at": 1}
            ).sort("preprocessed_at", -1)
        )
        if len(to_remove) <= 1:
            continue
        keep_preprocessed_at = to_remove[0].get("preprocessed_at")
        job_ids_changed += 1
        docs_deleted += len(to_remove) - 1
        logger.info(
            "jobData job_id=%s docs=%d, keeping newest(preprocessed_at=%s)",
            job_id,
            len(to_remove),
            keep_preprocessed_at,
        )
        if apply:
            for candidate in to_remove[1:]:
                jobs_collection.delete_one({"_id": candidate["_id"]})
    return job_ids_changed, docs_deleted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="commit changes to Mongo (default: dry-run)"
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"

    users_checked, users_changed, entries_removed = cleanup_users(args.apply)
    logger.info(
        "[%s] matches dedupe: checked=%d users, changed=%d, removed=%d entries",
        mode,
        users_checked,
        users_changed,
        entries_removed,
    )

    job_ids, jobs_removed = cleanup_jobs(args.apply)
    logger.info(
        "[%s] jobData dedupe: %d duplicated job_id(s), removed %d doc(s)",
        mode,
        job_ids,
        jobs_removed,
    )

    if not args.apply:
        logger.info("Dry-run only — rerun with --apply to commit.")


if __name__ == "__main__":
    main()