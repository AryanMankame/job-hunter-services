import os
from pymongo import MongoClient
mongo_username = "admin"
mongo_password = "admin"
connection_string = f"mongodb+srv://{mongo_username}:{mongo_password}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
client = MongoClient(connection_string)
db = client['jobHunter']
matches = db['resumeData'].find_one({"email": "temporaryemail2012000@gmail.com"})['matches']
job_counts = {}

for match in matches:
    if "job_id" in match:
        job_id = str(match["job_id"])
        job_counts[job_id] = job_counts.get(job_id, 0) + 1


# Write duplicate entries to text file
with open("duplicate_jobs.txt", "w", encoding="utf-8") as file:

    for match in matches:
        if "job_id" in match:
            job_id = str(match["job_id"])

            # Only write entries whose job_id occurs more than once
            if job_counts[job_id] > 1:
                file.write(f"{match}\n")
                file.write("-" * 100 + "\n")

print("Duplicate entries written to duplicate_jobs.txt")