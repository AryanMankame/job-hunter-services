import os
from pymongo import MongoClient

# MongoDB connection settings
mongo_username = "admin"
mongo_password = "admin"

# Connection string with appName parameter (optional)
connection_string = f"mongodb+srv://{mongo_username}:{mongo_password}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"

# Establish a connection to the MongoDB cluster
client = MongoClient(connection_string)

# Select the database
db = client['jobHunter']

import time
db['resumeData'].delete_one({"email" : "temporaryemail2012000@gmail.com"})
# start = time.time()
# count = db["jobData"].count_documents({})
# print("count_documents:", time.time() - start, "seconds, count =", count)

# start = time.time()
# docs = list(db["jobData"].find({},batch_size=5000).limit(1000))
# print("fetch all jobs:", time.time() - start, "seconds, docs =", len(docs))
# .find_one({"email": "temporaryemail2012000@gmail.com"})['matches']
# job_counts = {}

# for match in matches:
#     if "job_id" in match:
#         job_id = str(match["job_id"])
#         job_counts[job_id] = job_counts.get(job_id, 0) + 1


# # Write duplicate entries to text file
# with open("duplicate_jobs.txt", "w", encoding="utf-8") as file:

#     for match in matches:
#         if "job_id" in match:
#             job_id = str(match["job_id"])

#             # Only write entries whose job_id occurs more than once
#             if job_counts[job_id] > 1:
#                 file.write(f"{match}\n")
#                 file.write("-" * 100 + "\n")

# print("Duplicate entries written to duplicate_jobs.txt")