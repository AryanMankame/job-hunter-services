from unittest.mock import Mock
from fastapi.testclient import TestClient
from bson.objectid import ObjectId

from orchestration import app as app_module

client = TestClient(app_module.app)

SAMPLE_USER = {
    "_id": ObjectId(),
    "email": "jane@example.com",
    "resume_data": {"parsed_resume": {"full_name": "Jane Doe", "skills": ["python"]}},
}

SAMPLE_JOB = {
    "job_id": "job-1",
    "company": "Acme",
    "title": "Software Engineer",
    "raw_description": "We need a software engineer.",
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        if self._exc:
            raise self._exc
        return self._response


def mock_db_with(*results):
    mock_db = Mock()
    mock_db.find.side_effect = list(results)
    return mock_db


def mock_billing_db(user=None, job=None, usage=None):
    """Mimic the database for /generateResume: routes find() by collection so
    the subscription/usage read and the caller's own lookups resolve correctly,
    with `generatedResumes` counting fed via find_many."""
    mock_db = Mock()

    def fake_find(query, collection_name):
        if collection_name == "resumeData":
            return user
        if collection_name == "jobData":
            return job
        return None

    mock_db.find.side_effect = fake_find
    mock_db.find_many.return_value = [] if usage is None else usage
    return mock_db


class TestHealthCheck:
    def test_health_check_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200


class TestGenerateResumeTrigger:
    def test_missing_user_returns_404(self, monkeypatch):
        monkeypatch.setattr(app_module, "database_service", mock_billing_db(None))
        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )
        assert resp.status_code == 404

    def test_missing_job_returns_404(self, monkeypatch):
        monkeypatch.setattr(app_module, "database_service", mock_billing_db(SAMPLE_USER, None))
        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )
        assert resp.status_code == 404

    def test_successful_trigger_creates_pending_record(self, monkeypatch):
        mock_db = mock_billing_db(SAMPLE_USER, SAMPLE_JOB)
        mock_db.create.return_value = ObjectId()
        monkeypatch.setattr(app_module, "database_service", mock_db)

        fake = FakeAsyncClient(FakeResponse(200, {"triggered": True}))
        monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda **kw: fake)

        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["triggered"] is True
        assert "id" in body

        created = mock_db.create.call_args[0][0]
        assert created["email"] == "jane@example.com"
        assert created["job_id"] == "job-1"
        assert created["status"] == "pending"
        assert created["url"] is None
        assert created["job_title"] == "Software Engineer"
        mock_db.update.assert_not_called()

    def test_service_error_marks_record_failed(self, monkeypatch):
        mock_db = mock_billing_db(SAMPLE_USER, SAMPLE_JOB)
        mock_db.create.return_value = ObjectId()
        monkeypatch.setattr(app_module, "database_service", mock_db)

        fake = FakeAsyncClient(FakeResponse(500, {"detail": "boom"}))
        monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda **kw: fake)

        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )

        assert resp.status_code == 500
        item = mock_db.update.call_args[0][2]
        assert item["status"] == "failed"

    def test_connection_error_marks_record_failed(self, monkeypatch):
        mock_db = mock_billing_db(SAMPLE_USER, SAMPLE_JOB)
        mock_db.create.return_value = ObjectId()
        monkeypatch.setattr(app_module, "database_service", mock_db)

        import httpx

        fake = FakeAsyncClient(exc=httpx.ConnectError("down"))
        monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda **kw: fake)

        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )

        assert resp.status_code == 502
        item = mock_db.update.call_args[0][2]
        assert item["status"] == "failed"

    def test_free_quota_exhausted_returns_402(self, monkeypatch):
        # Free tier allows 2 generations; simulate 2 completed resumes.
        mock_db = mock_billing_db(
            SAMPLE_USER, SAMPLE_JOB, usage=[{"status": "completed"}, {"status": "completed"}]
        )
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.post(
            "/generateResume",
            json={"email": "jane@example.com", "job_id": "job-1"},
        )

        assert resp.status_code == 402
        body = resp.json()
        detail = body["detail"]
        # FastAPI returns 422 detail key as string when using HTTPException(detail=dict)
        usage = detail.get("usage", {}) if isinstance(detail, dict) else {}
        assert usage.get("plan") == "free"
        assert usage.get("remaining") == 0


class TestGenerateResumeCallback:
    def test_invalid_secret_returns_403(self, monkeypatch):
        monkeypatch.setattr(app_module, "CALLBACK_SECRET", "s3cret")
        resp = client.post(
            "/generateResume/callback",
            json={"record_id": str(ObjectId()), "status": "completed"},
        )
        assert resp.status_code == 403

    def test_missing_record_returns_404(self, monkeypatch):
        monkeypatch.setattr(app_module, "CALLBACK_SECRET", "")
        monkeypatch.setattr(app_module, "database_service", mock_db_with(None))
        resp = client.post(
            "/generateResume/callback",
            json={"record_id": str(ObjectId()), "status": "completed"},
        )
        assert resp.status_code == 404

    def test_success_updates_record_and_pushes(self, monkeypatch):
        monkeypatch.setattr(app_module, "CALLBACK_SECRET", "")
        record_id = str(ObjectId())
        mock_db = Mock()
        mock_db.find.return_value = {"_id": ObjectId(), "email": "jane@example.com"}
        monkeypatch.setattr(app_module, "database_service", mock_db)

        mock_push = Mock()
        monkeypatch.setattr(app_module, "push_to_user", mock_push)

        resp = client.post(
            "/generateResume/callback",
            json={
                "record_id": record_id,
                "status": "completed",
                "url": "https://supabase.example.com/resumes/jane/a.pdf",
                "updated_resume": {"full_name": "Jane Doe"},
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        item = mock_db.update.call_args[0][2]
        assert item["status"] == "completed"
        assert item["url"] == "https://supabase.example.com/resumes/jane/a.pdf"
        assert item["updated_resume"] == {"full_name": "Jane Doe"}

        mock_push.assert_called_once()
        assert mock_push.call_args[0][0] == "jane@example.com"
        assert mock_push.call_args[0][1]["status"] == "completed"


class TestJobMatch:
    def test_invalid_email_returns_400(self, monkeypatch):
        mock_db = Mock()
        mock_db.find.side_effect = AssertionError("find should not run")
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.post("/jobMatch", json={"email": "not-an-email"})

        assert resp.status_code == 400
        mock_db.find.assert_not_called()

    def test_missing_user_returns_404(self, monkeypatch):
        monkeypatch.setattr(app_module, "database_service", mock_db_with(None))

        resp = client.post("/jobMatch", json={"email": "jane@example.com"})

        assert resp.status_code == 404

    def test_returns_persisted_matches_when_present(self, monkeypatch):
        user = {
            "_id": ObjectId(),
            "email": "jane@example.com",
            "resume_data": {"parsed_resume": {"skills": ["python"]}},
        }
        mock_db = Mock()
        mock_db.find.return_value = user
        mock_db.count.return_value = 1
        mock_db.find_many.return_value = [{"job_id": "m1", "score": 80}]
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.post("/jobMatch", json={"email": "jane@example.com"})

        assert resp.status_code == 200
        assert resp.json() == {"jobs": [{"job_id": "m1", "score": 80}]}
        mock_db.bulk_upsert_matches.assert_not_called()

    def test_scores_and_persists_jobs_on_first_load(self, monkeypatch):
        user = {
            "_id": ObjectId(),
            "email": "jane@example.com",
            "resume_data": {
                "parsed_resume": {
                    "skills": ["python", "docker", "aws"],
                    "total_experience_months": 60,
                }
            },
        }
        jobs = [
            {
                "_id": ObjectId(),
                "job_id": "job-1",
                "title": "Software Engineer",
                "extracted": {
                    "required_experience_years": 4,
                    "required_skills": ["python", "docker"],
                    "nice_to_have_skills": ["aws"],
                },
            },
            {
                "_id": ObjectId(),
                "job_id": "job-2",
                "title": "SRE",
                "extracted": {
                    "required_experience_years": 10,
                    "required_skills": ["terraform_xyz"],
                    "nice_to_have_skills": [],
                },
            },
        ]
        mock_db = Mock()
        mock_db.find.return_value = user
        mock_db.count.return_value = 0
        mock_db.find_many.return_value = jobs
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.post("/jobMatch", json={"email": "jane@example.com"})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["jobs"]) == 1
        kept = body["jobs"][0]
        assert kept["job_id"] == str(jobs[0]["_id"])
        assert kept["score"] > 50

        mock_db.bulk_upsert_matches.assert_called_once()
        assert mock_db.bulk_upsert_matches.call_args.args[0] == "jane@example.com"
        assert mock_db.bulk_upsert_matches.call_args.args[1] == body["jobs"]

    def test_service_error_returns_500(self, monkeypatch):
        mock_db = Mock()
        mock_db.find.side_effect = RuntimeError("db exploded")
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.post("/jobMatch", json={"email": "jane@example.com"})

        assert resp.status_code == 500


class TestListGeneratedResumes:
    def test_returns_sorted_records_with_stringified_ids(self, monkeypatch):
        records = [
            {
                "_id": ObjectId(),
                "email": "jane@example.com",
                "status": "completed",
                "url": "u1",
                "created_at": "2026-08-01T10:00:00",
            },
            {
                "_id": ObjectId(),
                "email": "jane@example.com",
                "status": "pending",
                "url": None,
                "created_at": "2026-08-01T11:00:00",
            },
        ]
        mock_db = Mock()
        mock_db.find_many.return_value = records
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/generatedResumes?email=jane@example.com")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["resumes"]) == 2
        assert body["resumes"][0]["status"] == "pending"
        for record in body["resumes"]:
            assert isinstance(record["_id"], str)

    def test_sweeps_stale_pending_records_to_failed(self, monkeypatch):
        mock_db = Mock()
        mock_db.find_many.return_value = []
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/generatedResumes?email=jane@example.com")

        assert resp.status_code == 200
        assert resp.json()["resumes"] == []
        mock_db.update_many.assert_called_once()
        update_query = mock_db.update_many.call_args.args[0]
        collection_name = mock_db.update_many.call_args.args[1]
        item = mock_db.update_many.call_args.args[2]
        assert collection_name == "generatedResumes"
        assert update_query["email"] == "jane@example.com"
        assert update_query["status"] == "pending"
        assert "$lt" in update_query["created_at"]
        assert item["status"] == "failed"
        assert item["error"] == "Generation timed out"


class TestJobsEndpoint:
    def test_invalid_email_returns_400(self, monkeypatch):
        mock_db = Mock()
        mock_db.count.side_effect = AssertionError("count should not run")
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/jobs?email=not-an-email")

        assert resp.status_code == 400
        mock_db.count.assert_not_called()

    def test_no_matches_returns_404(self, monkeypatch):
        mock_db = Mock()
        mock_db.count.return_value = 0
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/jobs?email=jane@example.com")

        assert resp.status_code == 404
        mock_db.find_many.assert_not_called()

    def _build(self):
        j1 = {
            "_id": ObjectId(),
            "job_id": "job-1",
            "title": "Software Engineer",
            "company": "Acme",
            "location": "Remote",
            "is_remote": True,
            "posted_at": "2026-08-01",
            "extracted": {"seniority_level": "mid"},
        }
        j2 = {
            "_id": ObjectId(),
            "job_id": "job-2",
            "title": "Backend Engineer",
            "company": "Globex",
            "location": "NYC",
            "is_remote": False,
            "posted_at": "2026-07-20",
            "extracted": {"seniority_level": "senior"},
        }
        match_docs = [
            {"job_id": str(j2["_id"]), "score": 95},
            {"job_id": str(j1["_id"]), "score": 80},
        ]
        return match_docs, [j1, j2]

    def _patch(self, monkeypatch, match_docs, jobs, total=None):
        mock_db = Mock()
        mock_db.count.return_value = total if total is not None else len(match_docs)

        def fake_find_many(query, collection, projection=None, sort=None, skip=None, limit=None):
            if collection == "matches":
                items = sorted(
                    match_docs, key=lambda m: m.get("score", 0), reverse=True
                )
                if skip:
                    items = items[skip:]
                if limit:
                    items = items[:limit]
                return items
            return jobs

        mock_db.find_many.side_effect = fake_find_many
        monkeypatch.setattr(app_module, "database_service", mock_db)
        return mock_db

    def _job_data_calls(self, mock_db):
        return [c for c in mock_db.find_many.call_args_list if c.args[1] == "jobData"]

    def test_resolves_refs_and_returns_page(self, monkeypatch):
        match_docs, jobs = self._build()
        mock_db = self._patch(monkeypatch, match_docs, jobs)

        resp = client.get("/jobs?email=jane@example.com&page=1&page_size=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["has_more"] is True
        assert body["meta"]["total"] == 2
        assert len(body["jobs"]) == 1
        job = body["jobs"][0]
        assert job["_id"] == str(jobs[1]["_id"])
        assert job["score"] == 95
        assert job["title"] == "Backend Engineer"

        query, collection, projection = self._job_data_calls(mock_db)[0].args
        assert collection == "jobData"
        assert projection == {"raw_description": 0}
        assert "_id" in query and "$in" in query["_id"]

    def test_page_two_has_more_false(self, monkeypatch):
        match_docs, jobs = self._build()
        mock_db = self._patch(monkeypatch, match_docs, jobs)

        resp = client.get("/jobs?email=jane@example.com&page=2&page_size=2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["jobs"] == []
        assert body["has_more"] is False
        assert body["total"] == 2
        assert self._job_data_calls(mock_db) == []

    def test_resolves_refs_and_returns_page_in_score_order(self, monkeypatch):
        match_docs, jobs = self._build()
        mock_db = self._patch(monkeypatch, match_docs, jobs)

        resp = client.get("/jobs?email=jane@example.com&page_size=10")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["has_more"] is False
        assert [j["title"] for j in body["jobs"]] == ["Backend Engineer", "Software Engineer"]
        assert body["jobs"][0]["score"] == 95
        assert body["jobs"][1]["score"] == 80

    def test_job_missing_from_job_data_is_skipped(self, monkeypatch):
        match_docs, jobs = self._build()
        mock_db = self._patch(monkeypatch, match_docs, [jobs[0]])

        resp = client.get("/jobs?email=jane@example.com")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert [j["title"] for j in body["jobs"]] == ["Software Engineer"]

    def test_db_error_returns_500(self, monkeypatch):
        mock_db = Mock()
        mock_db.count.side_effect = RuntimeError("db exploded")
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/jobs?email=jane@example.com")

        assert resp.status_code == 500


class TestUsageEndpoint:
    def test_invalid_email_returns_400(self, monkeypatch):
        mock_db = Mock()
        mock_db.find.side_effect = AssertionError("find should not run")
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/usage?email=not-an-email")

        assert resp.status_code == 400
        mock_db.find.assert_not_called()

    def test_returns_entitlement(self, monkeypatch):
        mock_db = Mock()
        mock_db.find.return_value = None
        mock_db.find_many.return_value = [{"_id": ObjectId()}]
        monkeypatch.setattr(app_module, "database_service", mock_db)

        resp = client.get("/usage?email=jane@example.com")

        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"] == "free"
        assert body["limit"] == 2
        assert body["used"] == 1
        assert body["remaining"] == 1


class TestPlansEndpoint:
    def test_returns_plans(self):
        resp = client.get("/plans")

        assert resp.status_code == 200
        plans = resp.json()["plans"]
        assert {p["id"] for p in plans} == {"free", "starter", "pro", "advanced"}
        free = next(p for p in plans if p["id"] == "free")
        assert free["amount_paise"] == 0
        for plan in plans:
            if plan["id"] == "free":
                continue
            assert plan["amount_paise"] > 0
            assert plan["generations"] > 0
            assert plan["interval"] > 0


class TestSubscriptionCreate:
    def test_disabled_when_payments_off(self, monkeypatch):
        monkeypatch.setattr(app_module, "PAYMENTS_ENABLED", False)

        resp = client.post(
            "/subscription/create", json={"email": "jane@example.com", "plan": "pro"}
        )

        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]["message"]

    def test_invalid_email_returns_400(self):
        resp = client.post(
            "/subscription/create", json={"email": "not-an-email", "plan": "pro"}
        )
        assert resp.status_code == 400

    def test_returns_checkout_and_key_id(self, monkeypatch):
        async def fake_create(db, email, plan):
            return {
                "subscription_id": "sub_123",
                "plan": "pro",
                "amount_paise": 49900,
                "currency": "INR",
                "interval_days": 30,
                "generations": 30,
            }

        monkeypatch.setattr(app_module, "create_subscription_session", fake_create)
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_123")

        resp = client.post(
            "/subscription/create", json={"email": "jane@example.com", "plan": "pro"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["key_id"] == "rzp_test_123"
        assert body["checkout"]["subscription_id"] == "sub_123"

    def test_unknown_plan_returns_400(self, monkeypatch):
        async def fake_create(db, email, plan):
            raise app_module.RazorpayError(f"Unknown plan: {plan}")

        monkeypatch.setattr(app_module, "create_subscription_session", fake_create)

        resp = client.post(
            "/subscription/create", json={"email": "jane@example.com", "plan": "gold"}
        )

        assert resp.status_code == 400
        assert "Unknown plan" in resp.json()["detail"]["message"]

    def test_missing_key_returns_500(self, monkeypatch):
        async def fake_create(db, email, plan):
            return {"subscription_id": "sub_123"}

        monkeypatch.setattr(app_module, "create_subscription_session", fake_create)
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)

        resp = client.post(
            "/subscription/create", json={"email": "jane@example.com", "plan": "pro"}
        )

        assert resp.status_code == 500


class TestSubscriptionCancel:
    def test_invalid_email_returns_400(self):
        resp = client.post("/subscription/cancel", json={"email": "not-an-email"})
        assert resp.status_code == 400

    def test_cancel_success(self, monkeypatch):
        async def fake_cancel(db, email):
            return {"cancelled": True, "plan": "pro", "ends_at": "2026-09-01T00:00:00"}

        monkeypatch.setattr(app_module, "cancel_subscription", fake_cancel)

        resp = client.post("/subscription/cancel", json={"email": "jane@example.com"})

        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

    def test_no_subscription_returns_400(self, monkeypatch):
        async def fake_cancel(db, email):
            raise app_module.RazorpayError("No active subscription found for user.")

        monkeypatch.setattr(app_module, "cancel_subscription", fake_cancel)

        resp = client.post("/subscription/cancel", json={"email": "jane@example.com"})

        assert resp.status_code == 400
        assert "No active subscription" in resp.json()["detail"]["message"]


class TestRazorpayWebhook:
    def test_missing_signature_returns_401(self):
        resp = client.post("/razorpay/webhook", json={"event": "subscription.activated"})
        assert resp.status_code == 401

    def test_valid_event_delegates(self, monkeypatch):
        monkeypatch.setattr(app_module, "verify_webhook_signature", lambda *a: True)
        captured = {}

        def fake_handle(db, event):
            captured["event"] = event
            return {"ok": True, "activated": "pro"}

        monkeypatch.setattr(app_module, "handle_webhook_event", fake_handle)

        resp = client.post(
            "/razorpay/webhook",
            content=b'{"event": "subscription.activated"}',
            headers={"X-Razorpay-Signature": "sig"},
        )

        assert resp.status_code == 200
        assert resp.json()["activated"] == "pro"
        assert captured["event"]["event"] == "subscription.activated"

    def test_bad_json_returns_500(self, monkeypatch):
        monkeypatch.setattr(app_module, "verify_webhook_signature", lambda *a: True)

        resp = client.post(
            "/razorpay/webhook",
            content=b"not json",
            headers={"X-Razorpay-Signature": "sig"},
        )

        assert resp.status_code == 500
