"""Simulate a Razorpay webhook locally (test mode).

Usage:
    python orchestration/tests/simulate_webhook.py <event> <email> [subscription_id]

Events: subscription.activated | subscription.charged | payment.captured |
        subscription.cancelled | subscription.failed

The signature is computed with RAZORPAY_WEBHOOK_SECRET from server/.env, so it
exercises the same HMAC path as the real Razorpay webhook endpoint.
"""
import hashlib
import hmac
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

EVENT = sys.argv[1] if len(sys.argv) > 1 else "subscription.activated"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "test@example.com"
SUB_ID = sys.argv[3] if len(sys.argv) > 3 else "sub_test_placeholder"
URL = os.getenv("WEBHOOK_URL", "http://localhost:8000/razorpay/webhook")
SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

import time

event = {
    "event": EVENT,
    "id": f"event_sim_{EVENT}_{EMAIL}_{int(time.time())}",
    "payload": {
        "subscription": {
            "entity": {
                "id": SUB_ID,
                "status": "active",
                "plan": {"item": {"name": "jobhunter-advanced"}},
                "notes": {"email": EMAIL},
            }
        }
    },
}

body = json.dumps(event).encode("utf-8")
signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

resp = httpx.post(
    URL,
    content=body,
    headers={"X-Razorpay-Signature": signature},
    timeout=15.0,
)
print(resp.status_code, resp.json())