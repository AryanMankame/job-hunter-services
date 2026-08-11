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
