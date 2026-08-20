"""One-time migration: move every user's `matches` array out of resumeData into
a dedicated `matches` collection ({email, job_id, score}).

Why: the embedded `matches` array grew to ~3.5 MB per user (legacy entries embed
the full job doc), which made reading resumeData take ~37s and dominated the
`/jobs` response time. After this migration, resumeData is small and `/jobs`
reads only the indexed `matches` collection.

Canonical key: `job_id` is the `jobData._id` string (ObjectId hex). Legacy
entries are resolved against `jobData` by `_id` or by the network `job_id`
field (batched, so it is fast). Stale references (job already deleted) are
preserved with their raw key.

Usage:
    python migrate_matches.py            # dry-run (reports what would change)
    python migrate_matches.py --apply    # commit
"""
import os
import sys
import datetime
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "cronjob", ".env"))

MODIFY = "--apply" in sys.argv


def _dedupe_ops(ops):
    """Collapse duplicate (email, job_id) upserts, keeping the highest score."""
    best = {}
    for op in ops:
        key = (op._filter["email"], op._filter["job_id"])
        score = op._doc["$max"]["score"]
        if key not in best or score > best[key]._doc["$max"]["score"]:
            best[key] = op
    return list(best.values())


def main():
    from pymongo import MongoClient
    from pymongo.operations import UpdateOne
    from bson.objectid import ObjectId

    user = os.getenv("MONGO_USERNAME") or "admin"
    pwd = os.getenv("MONGO_PASSWORD") or "admin"
    client = MongoClient(
        f"mongodb+srv://{user}:{pwd}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0",
        serverSelectionTimeoutMS=20000,
    )
    db = client["jobHunter"]
    users = db["resumeData"]
    jobs = db["jobData"]
    matches_coll = db["matches"]
    backup = db[f"resumeData_matches_backup_{datetime.date.today().isoformat()}"]

    # 1. Indexes
    print("=== Creating indexes ===")
    matches_coll.create_index([("email", 1), ("job_id", 1)], unique=True, name="email_job_id_unique")
    matches_coll.create_index([("email", 1), ("score", -1)], name="email_score")
    matches_coll.create_index([("job_id", 1)], name="job_id")
    users.create_index([("email", 1)], name="email")
    jobs.create_index([("job_id", 1)], name="job_id")

    # 2. Build a batched resolution map: match key -> canonical jobData._id str.
    def build_resolution_map(all_keys):
        """Return {key: canonical_job_id} for every distinct match key."""
        obj_keys = [k for k in all_keys if ObjectId.is_valid(k)]
        net_keys = [k for k in all_keys if not ObjectId.is_valid(k)]

        resolution = {k: k for k in all_keys}  # default: preserve raw key

        if obj_keys:
            for doc in jobs.find({"_id": {"$in": [ObjectId(k) for k in obj_keys]}}, {"_id": 1}):
                resolution[str(doc["_id"])] = str(doc["_id"])
        if net_keys:
            for doc in jobs.find({"job_id": {"$in": net_keys}}, {"_id": 1, "job_id": 1}):
                resolution.setdefault(doc.get("job_id"), str(doc["_id"]))
        return resolution

    checked = migrated = skipped = stripped = 0
    ops = []
    for user_doc in users.find({}, {"_id": 1, "email": 1, "matches": 1}):
        checked += 1
        email = user_doc.get("email")
        matches = user_doc.get("matches") or []
        if not email:
            continue

        # Back up the raw array for safety.
        backup.update_one(
            {"email": email},
            {"$set": {"email": email, "matches": matches, "_id": user_doc["_id"]}},
            upsert=True,
        )

        if not matches:
            skipped += 1
            continue

        keys = {
            str(m.get("job_id") or m.get("_id") or "")
            for m in matches
            if isinstance(m, dict)
        }
        keys.discard("")
        resolution = build_resolution_map(keys)

        new_ops = 0
        for match in matches:
            if not isinstance(match, dict):
                continue
            raw_key = str(match.get("job_id") or match.get("_id") or "")
            if not raw_key:
                continue
            job_id = resolution[raw_key]
            score = match.get("score", 0)
            if isinstance(score, str) and score.isdigit():
                score = int(score)
            if not isinstance(score, (int, float)):
                score = 0
            ops.append(
                UpdateOne(
                    {"email": email, "job_id": job_id},
                    {
                        "$setOnInsert": {"email": email, "job_id": job_id},
                        "$max": {"score": score},
                    },
                    upsert=True,
                )
            )
            new_ops += 1
        migrated += 1
        print(f"  {'WOULD ' if not MODIFY else ''}migrate {email}: {len(matches)} matches")

        if len(ops) >= 500:
            if MODIFY:
                matches_coll.bulk_write(_dedupe_ops(ops), ordered=False)
            ops = []

    if ops:
        if MODIFY:
            matches_coll.bulk_write(_dedupe_ops(ops), ordered=False)

    # 3. Strip the now-redundant matches field from resumeData.
    if MODIFY:
        result = users.update_many(
            {"matches": {"$exists": True}},
            {"$unset": {"matches": ""}},
        )
        stripped = result.modified_count

    print(
        f"\nChecked {checked} user(s), {migrated} with matches to migrate, "
        f"{skipped} empty. Stripped matches from {stripped} resumeData doc(s)."
    )
    if not MODIFY:
        print("Dry-run only. Re-run with --apply to commit.")
    else:
        print(f"matches collection now has {matches_coll.count_documents({})} docs.")


if __name__ == "__main__":
    main()