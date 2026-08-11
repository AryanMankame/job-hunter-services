from unittest.mock import Mock
from fastapi.testclient import TestClient

from generateResume import app as app_module

client = TestClient(app_module.app)

VALID_PAYLOAD = {
    "record_id": "5f6a7b8c9d0e1f2a3b4c5d6e",
    "email": "jane@example.com",
    "job_id": "job-1",
    "resume_data": {"full_name": "Jane Doe", "skills": ["python"]},
    "job_description": "We need a software engineer.",
}


class FakeLambdaClient:
    def __init__(self):
        self.invocations = []

    def invoke(self, **kwargs):
        self.invocations.append(kwargs)


class TestHealthCheck:
    def test_health_check_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200


class TestGenerate:
    def test_successful_trigger_invokes_worker(self, monkeypatch):
        fake = FakeLambdaClient()
        monkeypatch.setattr(app_module.boto3, "client", lambda *a, **k: fake)

        resp = client.post("/generate", json=VALID_PAYLOAD)

        assert resp.status_code == 200
        assert resp.json() == {"triggered": True}

        invocation = fake.invocations[0]
        assert invocation["FunctionName"] == app_module.LAMBDA_FUNCTION_NAME
        assert invocation["InvocationType"] == "Event"
        import json

        payload = json.loads(invocation["Payload"])
        assert payload["record_id"] == VALID_PAYLOAD["record_id"]
        assert payload["email"] == VALID_PAYLOAD["email"]
        assert payload["job_description"] == VALID_PAYLOAD["job_description"]

    def test_invoke_failure_returns_502(self, monkeypatch):
        def boom(*args, **kwargs):
            raise Exception("lambda down")

        monkeypatch.setattr(app_module.boto3, "client", boom)

        resp = client.post("/generate", json=VALID_PAYLOAD)

        assert resp.status_code == 502

    def test_worker_url_uses_signed_post(self, monkeypatch):
        monkeypatch.setattr(app_module, "WORKER_FUNCTION_URL", "https://worker.example")
        fake_auth = object()
        monkeypatch.setattr(app_module, "_aws4_auth", lambda: fake_auth)
        calls = {}

        def fake_post(url, **kwargs):
            calls["url"] = url
            calls["kwargs"] = kwargs
            return Mock(status_code=200)

        monkeypatch.setattr(app_module.requests, "post", fake_post)

        resp = client.post("/generate", json=VALID_PAYLOAD)

        assert resp.status_code == 200
        assert resp.json() == {"triggered": True}
        assert calls["url"] == "https://worker.example/"
        assert calls["kwargs"]["auth"] is fake_auth
        assert calls["kwargs"]["json"]["record_id"] == VALID_PAYLOAD["record_id"]
        assert calls["kwargs"]["json"]["email"] == VALID_PAYLOAD["email"]
        assert (
            calls["kwargs"]["json"]["job_description"]
            == VALID_PAYLOAD["job_description"]
        )

    def test_worker_url_error_returns_502(self, monkeypatch):
        monkeypatch.setattr(app_module, "WORKER_FUNCTION_URL", "https://worker.example")
        monkeypatch.setattr(app_module, "_aws4_auth", lambda: object())
        monkeypatch.setattr(
            app_module.requests,
            "post",
            lambda *a, **k: Mock(status_code=500, text="boom"),
        )

        resp = client.post("/generate", json=VALID_PAYLOAD)

        assert resp.status_code == 502
        assert "boom" in resp.json()["detail"]
