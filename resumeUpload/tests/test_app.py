import io
import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient

import app as app_module
from app import app

client = TestClient(app)

VALID_EMAIL = "jane@example.com"
FAKE_PDF_BYTES = b"%PDF-1.4 fake-but-content-type-is-what-app.py-checks"


def upload(file_bytes=FAKE_PDF_BYTES, content_type="application/pdf",
           email=VALID_EMAIL, filename="resume.pdf"):
    return client.post(
        "/upload",
        files={"file": (filename, io.BytesIO(file_bytes), content_type)},
        data={"email": email},
    )


class TestRootEndpoint:
    def test_root_returns_welcome_message(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "message" in resp.json()


class TestUploadValidation:
    """These hit real validation code in app.py before any service call,
    so nothing needs mocking here."""

    def test_rejects_non_pdf_content_type(self):
        resp = upload(content_type="image/png")
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]

    def test_rejects_file_over_5mb(self):
        big_file = b"a" * (5 * 1024 * 1024 + 1)
        resp = upload(file_bytes=big_file)
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    def test_rejects_invalid_email_format(self):
        resp = upload(email="not-an-email")
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()

    def test_missing_email_field_is_422_from_fastapi(self):
        # Form(...) makes email required - omitting it entirely should be
        # FastAPI's own validation error, not app.py's custom 400
        resp = client.post(
            "/upload",
            files={"file": ("resume.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")},
        )
        assert resp.status_code == 422


class TestUploadHappyPath:

    def test_successful_upload_returns_200_and_id(self, monkeypatch):
        mock_service = Mock()
        mock_service.get_text_from_pdf.return_value = "extracted resume text"
        mock_service.parse_resume.return_value = Mock()
        mock_service.save_to_mongodb.return_value = "generated-id-123"
        monkeypatch.setattr(app_module, "resume_service", mock_service)

        resp = upload()

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "generated-id-123"
        assert "success" in body["message"].lower()

        mock_service.get_text_from_pdf.assert_called_once()
        mock_service.parse_resume.assert_called_once()
        mock_service.save_to_mongodb.assert_called_once()


class TestUploadFailurePaths:
    """Each of these isolates ONE failure point by mocking the service so
    only that step fails, confirming app.py maps it to the right status code."""

    def test_empty_extracted_text_returns_422(self, monkeypatch):
        mock_service = Mock()
        mock_service.get_text_from_pdf.return_value = "   "  # whitespace only
        monkeypatch.setattr(app_module, "resume_service", mock_service)

        resp = upload()

        assert resp.status_code == 422
        assert "extract" in resp.json()["detail"].lower()
        # parse/save should never be reached if extraction produced nothing
        mock_service.parse_resume.assert_not_called()
        mock_service.save_to_mongodb.assert_not_called()

    def test_pdf_extraction_crash_returns_502(self, monkeypatch):
        mock_service = Mock()
        mock_service.get_text_from_pdf.side_effect = Exception("pypdf blew up")
        monkeypatch.setattr(app_module, "resume_service", mock_service)

        resp = upload()

        assert resp.status_code == 502

    def test_llm_parsing_failure_returns_422(self, monkeypatch):
        mock_service = Mock()
        mock_service.get_text_from_pdf.return_value = "valid extracted text"
        mock_service.parse_resume.side_effect = Exception("LLM returned garbage")
        monkeypatch.setattr(app_module, "resume_service", mock_service)

        resp = upload()

        assert resp.status_code == 422
        mock_service.save_to_mongodb.assert_not_called()

    def test_mongo_save_failure_returns_502(self, monkeypatch):
        mock_service = Mock()
        mock_service.get_text_from_pdf.return_value = "valid extracted text"
        mock_service.parse_resume.return_value = Mock()
        mock_service.save_to_mongodb.side_effect = Exception("mongo down")
        monkeypatch.setattr(app_module, "resume_service", mock_service)

        resp = upload()

        assert resp.status_code == 502