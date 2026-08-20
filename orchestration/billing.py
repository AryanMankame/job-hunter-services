"""Subscription billing, quotas, and Razorpay integration.

Plans (paid monthly, Indian Rupees via Razorpay which supports card + UPI):
  - free     : 2 one-time resume generations
  - starter  : 20 generations / 30 days
  - pro      : 100 generations / 30 days
  - advanced : 250 generations / 30 days

Quota usage is computed from the `generatedResumes` collection so it is
idempotent and survives worker failures (failed runs never consume quota).
"""
import hmac
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

# ─── Plans ────────────────────────────────────────────────────────────────────

FREE_GENERATIONS = 2

# Razorpay requires total_count >= 1 and caps subscriptions at 100 years.
# 1200 monthly cycles = 100 years, effectively auto-recurring until cancelled.
MAX_SUBSCRIPTION_CYCLES = 1200

PLANS = {
    "free" : {
        "id": "free",
        "name": "Free",
        "amount_paise": 0,
        "currency": "INR",
        "interval": 30,
        "generations": FREE_GENERATIONS,
        "description": "2 resume generations every 30 days",
    },
    "starter": {
        "id": "starter",
        "name": "Starter",
        "amount_paise": 9900,  # ₹99.00
        "currency": "INR",
        "interval": 30,  # days
        "generations": 20,
        "description": "20 resume generations every 30 days",
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "amount_paise": 39900,  # ₹399.00
        "currency": "INR",
        "interval": 30,
        "generations": 100,
        "description": "100 resume generations every 30 days",
    },
    "advanced" : {
        "id": "advanced",
        "name": "Advanced",
        "amount_paise": 79900,  # ₹799.00
        "currency": "INR",
        "interval": 30,
        "generations": 250,
        "description": "250 resume generations every 30 days",
    }

}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_paid_plan(plan: str) -> bool:
    return plan in PLANS


# ─── Entitlement (read path) ─────────────────────────────────────────────────

def get_entitlement(database_service, email: str) -> dict:
    """Return the user's current plan, quota and usage snapshot.

    A subscription is honoured only while it is `active` and its billing window
    has not lapsed; otherwise the user falls back to the free tier.
    """
    sub = database_service.find({"email": email}, "subscriptions")
    now = datetime.now(timezone.utc)

    plan_id = "free"
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    if sub and sub.get("status") in ("active", "cancelled") and is_paid_plan(
        sub.get("plan", "")
    ):
        end = _parse_iso(sub.get("period_end"))
        if end is None or end > now:
            plan_id = sub["plan"]
            period_start = sub.get("period_start")
            period_end = sub.get("period_end")

    plan = PLANS.get(plan_id)
    limit = plan["generations"] if plan else FREE_GENERATIONS

    query = {"email": email, "status": {"$ne": "failed"}}
    if period_start:
        query["created_at"] = {"$gte": period_start}
    used = len(database_service.find_many(query, "generatedResumes"))

    return {
        "plan": plan_id,
        "plan_name": plan["name"] if plan else "Free",
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "period_start": period_start,
        "period_end": period_end,
        "billing": sub.get("razorpay_subscription_id") if sub else None,
    }


def can_generate(database_service, email: str) -> dict:
    usage = get_entitlement(database_service, email)
    usage["allowed"] = usage["remaining"] > 0
    return usage


# ─── Razorpay client helpers ─────────────────────────────────────────────────

class RazorpayError(RuntimeError):
    pass


RAZORPAY_BASE = "https://api.razorpay.com/v1"


def _keys():
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise RazorpayError("Razorpay keys are not configured.")
    return key_id, key_secret


def _auth():
    key_id, key_secret = _keys()
    return (key_id, key_secret)


async def _rzp_get(client: httpx.AsyncClient, path: str):
    resp = await client.get(path)
    if resp.status_code >= 400:
        raise RazorpayError(f"Razorpay GET {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def _rzp_post(client: httpx.AsyncClient, path: str, payload: dict):
    resp = await client.post(path, json=payload)
    if resp.status_code >= 400:
        raise RazorpayError(f"Razorpay POST {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def get_or_create_plan(client: httpx.AsyncClient, plan_id: str) -> str:
    """Return a Razorpay plan endpoint id for our plan, creating it once."""
    plan = PLANS[plan_id]
    try:
        plans = await _rzp_get(client, "/plans")
        for item in plans.get("items", []):
            item_name = (item.get("item") or {}).get("name") or ""
            if item_name == f"jobhunter-{plan_id}":
                return item["id"]
    except RazorpayError:
        pass

    created = await _rzp_post(
        client,
        "/plans",
        {
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": f"jobhunter-{plan_id}",
                "description": plan["description"],
                "amount": plan["amount_paise"],
                "currency": plan["currency"],
            },
        },
    )
    return created["id"]


# ─── Subscription lifecycle ──────────────────────────────────────────────────

async def create_subscription_session(database_service, email: str, plan_id: str) -> dict:
    if not is_paid_plan(plan_id):
        raise RazorpayError(f"Unknown plan: {plan_id}")

    async with httpx.AsyncClient(base_url=RAZORPAY_BASE, auth=_auth(), timeout=30.0) as client:
        rzp_plan_id = await get_or_create_plan(client, plan_id)
        sub = await _rzp_post(
            client,
            "/subscriptions",
            {
                "plan_id": rzp_plan_id,
                "customer_notify": True,
                "total_count": MAX_SUBSCRIPTION_CYCLES,
                "notes": {"email": email, "plan": plan_id},
            },
        )

    database_service.insert(
        {
            "email": email,
            "plan": plan_id,
            "status": "pending",
            "razorpay_subscription_id": sub["id"],
            "razorpay_plan_id": rzp_plan_id,
            "period_start": None,
            "period_end": None,
            "created_at": _now(),
            "updated_at": _now(),
        },
        "subscriptions",
        {"email": email},
    )

    plan = PLANS[plan_id]
    return {
        "subscription_id": sub["id"],
        "plan": plan_id,
        "amount_paise": plan["amount_paise"],
        "currency": plan["currency"],
        "interval_days": plan["interval"],
        "generations": plan["generations"],
    }


async def cancel_subscription(database_service, email: str) -> dict:
    """Cancel the user's Razorpay subscription at the end of the billing cycle.

    The user keeps access until `period_end` (see `get_entitlement`), and the
    record is marked `cancelled` so the webhook/entitlement logic stays simple.
    """
    sub = database_service.find({"email": email}, "subscriptions")
    if not sub or not sub.get("razorpay_subscription_id"):
        raise RazorpayError("No active subscription found for user.")

    rzp_id = sub["razorpay_subscription_id"]
    async with httpx.AsyncClient(
        base_url=RAZORPAY_BASE, auth=_auth(), timeout=30.0
    ) as client:
        try:
            await _rzp_post(
                client,
                f"/subscriptions/{rzp_id}/cancel",
                {"cancel_at_cycle_end": True},
            )
        except RazorpayError:
            # Subscription may already be inactive at Razorpay; still mirror the
            # local state so the user sees the cancellation take effect.
            pass

    database_service.update(
        {"email": email},
        "subscriptions",
        {"status": "cancelled", "updated_at": _now()},
    )
    return {
        "cancelled": True,
        "plan": sub.get("plan"),
        "ends_at": sub.get("period_end"),
    }


def verify_webhook_signature(body_bytes: bytes, signature: Optional[str]) -> bool:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _match_plan(keyword: str) -> Optional[str]:
    low = keyword.lower().strip()
    if low.startswith("jobhunter-"):
        candidate = low[len("jobhunter-"):]
        if is_paid_plan(candidate):
            return candidate
    for plan_id in PLANS:
        if plan_id in low:
            return plan_id
    return None


def _find_subscription(database_service, email: Optional[str], rzp_sub_id: Optional[str]):
    if email:
        return database_service.find({"email": email}, "subscriptions")
    if rzp_sub_id:
        return database_service.find(
            {"razorpay_subscription_id": rzp_sub_id}, "subscriptions"
        )
    return None


def _activate(
    database_service,
    record: Optional[dict],
    email: Optional[str],
    rzp_sub_id: Optional[str],
    plan_id: str,
    event: dict,
) -> None:
    if not is_paid_plan(plan_id):
        plan_id = "pro"
    plan = PLANS[plan_id]

    now = _now()
    from datetime import timedelta
    period_end_iso = (
        datetime.now(timezone.utc) + timedelta(days=plan["interval"])
    ).replace(microsecond=0).isoformat()

    target_email = email
    if not target_email and record:
        target_email = record.get("email")
    if not target_email:
        # Fall back to note attached by Razorpay checkout.
        target_email = (event.get("payload", {}).get("payment", {}).get("entity", {})
                        .get("notes", {})).get("email")
    if not target_email:
        return

    database_service.insert(
        {
            "email": target_email,
            "plan": plan_id,
            "status": "active",
            "razorpay_subscription_id": rzp_sub_id,
            "period_start": now,
            "period_end": period_end_iso,
            "updated_at": now,
        },
        "subscriptions",
        {"email": target_email},
    )


def handle_webhook_event(database_service, event: dict) -> dict:
    payload = event.get("payload", {})
    sub_data = payload.get("subscription", {}).get("entity", {})
    if not sub_data:
        # payment.captured carries only a subscription_id on the payment entity.
        sub_data = payload.get("payment", {}).get("entity", {})
    notes = sub_data.get("notes", {}) or {}
    email = notes.get("email")
    rzp_sub_id = sub_data.get("id") or sub_data.get("subscription_id")

    record = _find_subscription(database_service, email, rzp_sub_id)
    event_name = event.get("event", "")
    result = {"ok": True}

    # Prevent duplicate processing of the same razorpay event.
    if record and record.get("last_event_id") == event.get("id"):
        return {"ok": True, "duplicate": True}

    if event_name in ("subscription.charged", "subscription.activated", "payment.captured"):
        plan_id = record.get("plan") if record else None
        if not plan_id:
            rzp_plan = sub_data.get("plan", {}) or {}
            rzp_item = (rzp_plan.get("item") or {}).get("name") or rzp_plan.get("name") or ""
            plan_id = _match_plan(rzp_item) or "pro"
        _activate(database_service, record, email, rzp_sub_id, plan_id, event)
        result["activated"] = plan_id

    elif event_name in ("subscription.cancelled", "subscription.completed", "subscription.halted"):
        target = record
        if target:
            database_service.update(
                {"email": target["email"]},
                "subscriptions",
                {"status": "cancelled", "updated_at": _now()},
            )
        result["cancelled"] = True

    elif event_name in ("subscription.failed", "payment.failed"):
        result["failed"] = True

    # Mark processed so retries are idempotent.
    if record and record.get("email"):
        database_service.update(
            {"email": record["email"]},
            "subscriptions",
            {"last_event_id": event.get("id"), "updated_at": _now()},
        )

    return result