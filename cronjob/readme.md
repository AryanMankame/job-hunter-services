Steps:
[*] Generate the query strings for various jobs & locations
[] Insert the jobs into mongodb collection with custom_id being the jobId so we only have unique job entries
[] After insert remove the jobs which were inserted more than 30 days ago
[] Deploy on vercel