import math
from common.skills import SkillsMatcher

def score_resume(resume_data: dict, job: dict, skillmatcher: SkillsMatcher) -> int:
    """
    Score a candidate's resume against a job posting.

    Args:
        resume_data: Parsed resume as a dict (e.g. ResumeData.model_dump(mode="json")).
        job: A job document containing an "extracted" block with
             required_experience_years, required_skills and nice_to_have_skills.
        skillmatcher: A SkillsMatcher instance used to compute the skills score.

    Returns:
        Integer score from 0-100, or 0 when the data is unusable.
    """
    try:
        extracted = job.get("extracted", {})
        yoe_required_by_job = extracted.get("required_experience_years")
        total_experience_months = resume_data.get("total_experience_months")
        if (
            yoe_required_by_job is not None
            and total_experience_months is not None
            and total_experience_months >= yoe_required_by_job * 12
        ):
            years_score = 1
        else:
            years_score = 0

        user_skills = resume_data.get("skills")
        required_skills = extracted.get("required_skills")
        nice_to_have_skills = extracted.get("nice_to_have_skills")
        if user_skills is None or required_skills is None:
            return 0

        skills_score = skillmatcher.calculate_skills_score(
            user_skills, required_skills, nice_to_have_skills
        )["skills_score"]
        return math.ceil(50 * years_score + 50 * skills_score)
    except Exception:
        return 0
