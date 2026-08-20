import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("MONGO_USERNAME", "test-user")
os.environ.setdefault("MONGO_PASSWORD", "test-pass")
os.environ.setdefault("LAMBDA_FUNCTION_NAME", "generateResumeWorker")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("PAYMENTS_ENABLED", "true")
