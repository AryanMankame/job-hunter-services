import copy
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import app as app_module
from app import app, score_resume
from common.models import ResumeData

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
    "_id": "job-1",
    "extracted": {
        "required_experience_years": 4,
        "required_skills": ["python", "docker"],
        "nice_to_have_skills": ["aws"],
    }
}


def make_matcher(skills_score=1.0):
    matcher = MagicMock()
    matcher.calculate_skills_score.return_value = {"skills_score": skills_score}
    return matcher


class TestAppImport:
    def test_app_imports_without_error(self):
        assert hasattr(app_module, "app")
        assert hasattr(app_module, "score_resume")
        assert hasattr(app_module, "find_matches")


class TestScoreResume:
    """Unit tests for the shared score_resume helper — no endpoints involved."""

    def test_yoe_match_yields_full_score(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python", "docker"]

        result = score_resume(resume, SAMPLE_JOB, make_matcher())

        assert result == 100

    def test_yoe_mismatch_yields_zero_years_score(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=24)
        resume["skills"] = ["python", "docker"]

        result = score_resume(resume, SAMPLE_JOB, make_matcher())

        assert result == 50

    def test_yoe_exactly_at_threshold_matches(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=48)
        resume["skills"] = ["python", "docker"]

        result = score_resume(resume, SAMPLE_JOB, make_matcher())

        assert result == 100

    def test_yoe_none_treated_as_mismatch(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python"]

        job = copy.deepcopy(SAMPLE_JOB)
        job["extracted"]["required_experience_years"] = None

        result = score_resume(resume, job, make_matcher())

        assert result == 50

    def test_user_skills_none_returns_zero(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = None

        result = score_resume(resume, SAMPLE_JOB, make_matcher())
        assert result == 0

    def test_required_skills_none_returns_zero(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python"]

        job = copy.deepcopy(SAMPLE_JOB)
        job["extracted"]["required_skills"] = None

        result = score_resume(resume, job, make_matcher())
        assert result == 0

    def test_skillmatcher_exception_returns_zero(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python", "docker"]

        matcher = MagicMock()
        matcher.calculate_skills_score.side_effect = ValueError("skillmatcher down")

        result = score_resume(resume, SAMPLE_JOB, matcher)

        assert result == 0

    def test_missing_extracted_key_returns_zero(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python"]

        result = score_resume(resume, {}, make_matcher())
        assert result == 0

    def test_partial_skills_score_uses_ceil(self):
        resume = dict(SAMPLE_RESUME_DATA, total_experience_months=60)
        resume["skills"] = ["python"]

        result = score_resume(resume, SAMPLE_JOB, make_matcher(skills_score=0.33))

        assert result == 67


class TestHealthCheck:
    def test_health_check_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Job Match Service is up!"}


class TestFindMatches:
    """Endpoint tests — no MongoDB involved; jobs arrive in the request body."""

    def test_invalid_email_returns_400(self):
        resp = client.post(
            "/findMatches",
            json={"email": "not-an-email", "resumeData": SAMPLE_RESUME_DATA, "jobs": []},
        )
        assert resp.status_code == 400
        assert "Invalid email format" in resp.json()["detail"]

    def test_returns_only_jobs_with_score_above_50(self):
        jobs = [
            dict(SAMPLE_JOB, _id="high-score"),
            dict(SAMPLE_JOB, _id="at-threshold"),
            {
                "_id": "low-score",
                "extracted": {
                    "required_experience_years": 10,
                    "required_skills": ["nonexistent_skill_xyz"],
                    "nice_to_have_skills": [],
                }
            },
        ]
        resp = client.post(
            "/findMatches",
            json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA, "jobs": jobs},
        )

        assert resp.status_code == 200
        assert len(resp.json()["filtered_list"]) == 2

    def test_empty_jobs_returns_empty_list(self):
        resp = client.post(
            "/findMatches",
            json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA, "jobs": []},
        )

        assert resp.status_code == 200
        assert resp.json() == {"filtered_list": []}

    def test_missing_jobs_field_defaults_to_empty(self):
        resp = client.post(
            "/findMatches",
            json={"email": "jane@example.com", "resumeData": SAMPLE_RESUME_DATA},
        )

        assert resp.status_code == 200
        assert resp.json() == {"filtered_list": []}
