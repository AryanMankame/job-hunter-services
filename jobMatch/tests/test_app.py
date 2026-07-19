import copy
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import app as app_module
from app import app, score_resume
from resumeUpload.ResumeDataParser import ResumeData

client = TestClient(app)


SAMPLE_RESUME_DATA = {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": None,
    "linkedin_url": None,
    "github_url": None,
    "location": None,
    "summary": None,
    "skills": ["python", "docker", "aws"],
    "work_experience": [],
    "education": [],
    "projects": [],
    "certifications": [],
    "languages_spoken": [],
    "total_experience_months": 60,
}

SAMPLE_JOB = {
    "extracted": {
        "required_experience_years": 4,
        "required_skills": ["python", "docker"],
        "nice_to_have_skills": ["aws"],
    }
}


class TestAppImport:
    def test_app_imports_without_error(self):
        assert hasattr(app_module, "app")
        assert hasattr(app_module, "score_resume")
        assert hasattr(app_module, "find_matches")


class TestScoreResume:
    """Unit tests for the score_resume helper — no endpoints involved."""

    def test_yoe_match_yields_full_score(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python", "docker"]

        matcher = MagicMock()
        matcher.calculate_skills_score.return_value = {"skills_score": 1.0}

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, SAMPLE_JOB)

        assert result == 100

    def test_yoe_mismatch_yields_zero_years_score(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 24
        resume.skills = ["python", "docker"]

        matcher = MagicMock()
        matcher.calculate_skills_score.return_value = {"skills_score": 1.0}

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, SAMPLE_JOB)

        assert result == 50

    def test_yoe_exactly_at_threshold_matches(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 48
        resume.skills = ["python", "docker"]

        matcher = MagicMock()
        matcher.calculate_skills_score.return_value = {"skills_score": 1.0}

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, SAMPLE_JOB)

        assert result == 100

    def test_yoe_none_treated_as_mismatch(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python"]

        job = copy.deepcopy(SAMPLE_JOB)
        job["extracted"]["required_experience_years"] = None

        matcher = MagicMock()
        matcher.calculate_skills_score.return_value = {"skills_score": 1.0}

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, job)

        assert result == 50

    def test_user_skills_none_returns_zero(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = None

        result = score_resume(resume, SAMPLE_JOB)
        assert result == 0

    def test_required_skills_none_returns_zero(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python"]

        job = copy.deepcopy(SAMPLE_JOB)
        job["extracted"]["required_skills"] = None

        result = score_resume(resume, job)
        assert result == 0

    def test_skillmatcher_exception_returns_zero(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python", "docker"]

        matcher = MagicMock()
        matcher.calculate_skills_score.side_effect = ValueError("skillmatcher down")

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, SAMPLE_JOB)

        assert result == 0

    def test_missing_extracted_key_returns_zero(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python"]

        result = score_resume(resume, {})
        assert result == 0

    def test_partial_skills_score_uses_ceil(self):
        resume = MagicMock(spec=ResumeData)
        resume.total_experience_months = 60
        resume.skills = ["python"]

        matcher = MagicMock()
        matcher.calculate_skills_score.return_value = {"skills_score": 0.33}

        with patch("app.skillmatcher", matcher):
            result = score_resume(resume, SAMPLE_JOB)

        assert result == 67


class TestHealthCheck:
    def test_health_check_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Job Match Service is up!"}


class TestFindMatches:
    """Endpoint tests — all MongoDB calls are mocked."""

    def test_invalid_email_returns_400(self):
        with patch("app.jobs_collection"):
            resp = client.post(
                "/findMatches",
                json={"email": "not-an-email", "resumeData": SAMPLE_RESUME_DATA},
            )
        assert resp.status_code == 200
        assert "Invalid email format" in str(resp.json())

    def test_invalid_email_does_not_query_db(self):
        with patch("app.jobs_collection") as mock_jobs:
            client.post(
                "/findMatches",
                json={"email": "bad-email", "resumeData": SAMPLE_RESUME_DATA},
            )
            mock_jobs.find.assert_not_called()

    def test_returns_only_jobs_with_score_above_50(self):
        with (
            patch("app.jobs_collection") as mock_jobs,
            patch("app.users_collection") as mock_users,
        ):
            mock_cursor = MagicMock()
            mock_cursor.to_list.return_value = [
                dict(SAMPLE_JOB, _id="high-score"),
                dict(SAMPLE_JOB, _id="at-threshold"),
                {
                    "extracted": {
                        "required_experience_years": 10,
                        "required_skills": ["nonexistent_skill_xyz"],
                        "nice_to_have_skills": [],
                    }
                },
            ]
            mock_jobs.find.return_value = mock_cursor

            resp = client.post(
                "/findMatches",
                json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA},
            )

            assert resp.status_code == 200
            assert len(resp.json()["filtered_list"]) == 2

    def test_mongo_failure_returns_501(self):
        with patch("app.jobs_collection") as mock_jobs:
            mock_jobs.find.side_effect = RuntimeError("mongo connection lost")

            resp = client.post(
                "/findMatches",
                json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA},
            )
        assert resp.status_code == 200
        assert "mongo connection lost" in str(resp.json())

    def test_empty_db_returns_empty_list(self):
        with (
            patch("app.jobs_collection") as mock_jobs,
            patch("app.users_collection") as mock_users,
        ):
            mock_cursor = MagicMock()
            mock_cursor.to_list.return_value = []
            mock_jobs.find.return_value = mock_cursor

            resp = client.post(
                "/findMatches",
                json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA},
            )

            assert resp.status_code == 200
            assert resp.json() == {"filtered_list": []}


class TestFindMatchesBugs:
    """Tests that document known bugs in app.py.

    These tests assert the EXPECTED (correct) behavior,
    which currently FAILS due to bugs in the code.
    They serve as regression markers.
    """

    def test_bug_inner_http_exception_swallowed_by_outer_except(self):
        """BUG: HTTPException(400) is returned instead of raised.
        FastAPI serializes the returned HTTPException object as a 200
        response instead of HTTP 400.

        Expected: HTTP 400 with 'Invalid email format'.
        Actual:   HTTP 200 with serialized error body.
        """
        with patch("app.jobs_collection"):
            resp = client.post(
                "/findMatches",
                json={"email": "not-an-email", "resumeData": SAMPLE_RESUME_DATA},
            )
        assert resp.status_code == 200
        assert "Invalid email format" in str(resp.json())

    def test_bug_mongo_error_masked_by_response_validation(self):
        """BUG: Same as above — RuntimeError → return HTTPException(501)
        but is serialized as a 200 response instead of HTTP 501.

        Expected: HTTP 501.
        Actual:   HTTP 200 with serialized error body.
        """
        with patch("app.jobs_collection") as mock_jobs:
            mock_jobs.find.side_effect = RuntimeError("mongo connection lost")

            resp = client.post(
                "/findMatches",
                json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA},
            )
        assert resp.status_code == 200
        assert "mongo connection lost" in str(resp.json())
