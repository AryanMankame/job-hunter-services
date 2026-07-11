import re
from typing import Dict, List

class JobPreprocessor:
    """
    Extracts structured data from raw job descriptions.
    """

    SKILL_DATABASE = {
        "languages": [
            "python", "java", "javascript", "typescript", "go", "golang", "rust",
            "c++", "c#", "php", "ruby", "kotlin", "swift", "scala", "perl",
            "r", "matlab", "groovy", "lua", "julia", "haskell", "elixir", "clojure"
        ],
        "frameworks": [
            "django", "fastapi", "flask", "spring", "spring boot",
            "react", "vue", "angular", "svelte", "next", "nextjs",
            "express", "node.js", "nest", "fastify",
            "rails", "sinatra", "laravel", "symfony",
            "asp.net", ".net core", "asp.net core", "dotnet"
        ],
        "databases": [
            "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
            "dynamodb", "cassandra", "firestore", "oracle", "mssql",
            "sqlite", "neo4j", "cockroachdb", "kafka", "postgres"
        ],
        "cloud": [
            "aws", "azure", "gcp", "google cloud", "heroku",
            "kubernetes", "k8s", "docker", "terraform", "ansible"
        ],
        "tools": [
            "git", "jira", "jenkins", "datadog", "prometheus",
            "grafana", "splunk", "newrelic", "gitlab", "github"
        ],
        "methodologies": [
            "agile", "scrum", "kanban", "tdd", "bdd", "cicd", "ci/cd"
        ]
    }

    SKILL_ALIASES = {
        "js": "javascript",
        "ts": "typescript",
        "py": "python",
        "pg": "postgresql",
        "mongo": "mongodb",
        "k8s": "kubernetes",
        "node": "node.js",
        ".net": "asp.net",
        "gcp": "google cloud",
    }

    def __init__(self):
        self.all_skills = self._flatten_skills()

    def _flatten_skills(self) -> List[str]:
        """Create flat list of all skills for quick lookup"""
        skills = []
        for category_skills in self.SKILL_DATABASE.values():
            skills.extend(category_skills)
        skills.extend(self.SKILL_ALIASES.keys())
        return skills

    def preprocess_job(self, job_json: dict) -> dict:
        """
        Extract structured data from raw job.
        Returns: dict with extracted fields ready for MongoDB.
        """

        job_description = job_json.get("job_description", "")

        return {
            # Keep original fields
            "job_id": job_json.get("job_id"),
            "title": job_json.get("job_title"),
            "company": job_json.get("employer_name"),
            "url": job_json.get("job_apply_link"),
            "posted_at": job_json.get("job_posted_at_datetime_utc"),
            "location": job_json.get("job_location"),
            "is_remote": job_json.get("job_is_remote", False),
            "source": job_json.get("job_publisher", "unknown"),

            # Extracted fields
            "extracted": {
                "required_experience_years": self._extract_experience_years(job_description),
                "seniority_level": self._extract_seniority(job_description),
                "required_skills": self._extract_required_skills(job_description),
                "nice_to_have_skills": self._extract_nice_to_have_skills(job_description),
                "education_level": self._extract_education(job_description),
                "employment_type": self._extract_employment_type(job_json),
            },

            "raw_description": job_description,
            "preprocessed_at": datetime.datetime.utcnow(),
        }

    def _extract_experience_years(self, text: str) -> int:
        """
        Extract required years of experience.
        Handles: "3+ years", "2-4 years", "5 years", etc.
        """

        patterns = [
            r'(\d+)\s*\+\s*years?',  # "3+ years"
            r'(\d+)\s*-\s*(\d+)\s*years?',  # "2-4 years"
            r'(\d+)\s*years?',  # "5 years"
        ]

        for i, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if i == 1:  # Range: take minimum
                    return int(match.group(1))
                else:  # Single number
                    return int(match.group(1))

        return 0  # Default if not found

    def _extract_seniority(self, text: str) -> str:
        """
        Infer seniority level from text.
        Returns: "junior", "mid", "senior", "lead"
        """

        text_lower = text.lower()

        if any(word in text_lower for word in ["fresher", "entry", "0-1 year", "0-2 year", "graduate"]):
            return "junior"
        elif any(word in text_lower for word in ["principal", "staff", "architect", "director"]):
            return "lead"
        elif any(word in text_lower for word in ["senior", "sr.", "10+ years", "8+ years"]):
            return "senior"
        elif any(word in text_lower for word in ["lead engineer", "team lead"]):
            return "lead"
        else:
            return "mid"

    def _extract_required_skills(self, text: str) -> list:
        """
        Extract skills from "Must have" or "Required" section.
        """

        text_lower = text.lower()

        # Split on "nice to have" to isolate required section
        required_section = text_lower.split("nice to have")[0]
        required_section = required_section.split("preferred")[0]

        found_skills = set()

        for skill in self.all_skills:
            if skill in required_section:
                # Handle aliases
                canonical_skill = self.SKILL_ALIASES.get(skill, skill)
                found_skills.add(canonical_skill)

        return list(found_skills)

    def _extract_nice_to_have_skills(self, text: str) -> list:
        """
        Extract skills from "Nice to have" or "Preferred" section.
        """

        text_lower = text.lower()

        # Extract everything after "nice to have" or "preferred"
        if "nice to have" in text_lower:
            nice_section = text_lower.split("nice to have")[1]
        elif "preferred" in text_lower:
            nice_section = text_lower.split("preferred")[1]
        else:
            return []

        found_skills = set()

        for skill in self.all_skills:
            if skill in nice_section:
                canonical_skill = self.SKILL_ALIASES.get(skill, skill)
                found_skills.add(canonical_skill)

        return list(found_skills)

    def _extract_education(self, text: str) -> str:
        """
        Extract minimum education requirement.
        Returns: "high_school", "bachelor", "master", "phd", "any"
        """

        text_lower = text.lower()

        if "phd" in text_lower or "doctorate" in text_lower:
            return "phd"
        elif "master" in text_lower or "m.tech" in text_lower or "m.s." in text_lower:
            return "master"
        elif "bachelor" in text_lower or "b.tech" in text_lower or "b.s." in text_lower or "degree" in text_lower:
            return "bachelor"
        elif "high school" in text_lower or "diploma" in text_lower:
            return "high_school"
        else:
            return "any"

    def _extract_employment_type(self, job_json: dict) -> str:
        """
        Extract employment type from job.
        """
        emp_type = job_json.get("job_employment_type", "Full-time")

        if "full" in emp_type.lower():
            return "full_time"
        elif "part" in emp_type.lower():
            return "part_time"
        elif "contract" in emp_type.lower():
            return "contract"
        elif "intern" in emp_type.lower():
            return "internship"
        else:
            return "full_time"

