"""One-time migration: slim every resumeData.matches entry to {job_id, score}.

Previously the `matches` array inside each resumeData document stored the FULL
job object for every match (~1200 jobs per user), which made the documents
multi-MB and extremely slow to read. This rewrites each match down to just
`{job_id, score}`, keeping the canonical job bodies in the `jobData` collection.

Usage:
    python slim_matches.py            # dry-run (reports what would change)
    python slim_matches.py --apply    # commit the changes
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

MODIFY = "--apply" in sys.argv


def is_slim(match) -> bool:
    """True if match is already the compact {job_id, score} shape."""
    if not isinstance(match, dict):
        return False
    return "job_id" in match and not any(
        k in match for k in ("title", "company", "extracted", "raw_description")
    )


def slim(match) -> dict:
    if not isinstance(match, dict):
        return {}
    return {
        "job_id": match.get("job_id"),
        "score": match.get("score", 0),
    }


def main():
    from pymongo import MongoClient

    user = os.getenv("MONGO_USERNAME")
    pwd = os.getenv("MONGO_PASSWORD")
    client = MongoClient(
        f"mongodb+srv://{user}:{pwd}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
    )
    db = client["jobHunter"]
    users = db["resumeData"]

    checked = changed = 0
    total_before = total_after = 0
    dry = not MODIFY

    for user_doc in users.find({}, {"_id": 1, "matches": 1}):
        checked += 1
        matches = user_doc.get("matches") or []
        if not matches:
            continue
        new_matches = [slim(m) for m in matches]
        new_matches = [m for m in new_matches if m.get("job_id")]
        total_before += len(matches)
        total_after += len(new_matches)
        if is_slim(matches[0]):
            print(f"  [skip] {user_doc['_id']}: already slim ({len(matches)} matches)")
            continue
        changed += 1
        print(
            f"  {'WOULD ' if dry else ''}slim {user_doc['_id']}: "
            f"{len(matches)} -> {len(new_matches)} matches"
        )
        if not dry:
            users.update_one({"_id": user_doc["_id"]}, {"$set": {"matches": new_matches}})

    print(f"\nChecked {checked} user(s), {changed} need slim, "
          f"matches {total_before} -> {total_after}.")
    if dry:
        print("Dry-run only. Re-run with --apply to commit.")


if __name__ == "__main__":
    main()