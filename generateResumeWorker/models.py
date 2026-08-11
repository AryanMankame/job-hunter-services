from pydantic import BaseModel, Field
from typing import Optional
from common.models import ResumeData
class AppliedChange(BaseModel):
    section: str = Field(description="Which section was changed e.g. 'skills', 'summary', 'work_experience'")
    change_description: str = Field(description="What exactly was changed and why")

class SkippedSuggestion(BaseModel):
    suggestion: str = Field(description="The original suggestion text")
    reason: str = Field(description="Why it was skipped — what information is missing to apply it safely")

class ResumeUpdateResult(BaseModel):
    updated_resume: ResumeData
    applied_changes: list[AppliedChange] = Field(
        description="List of changes actually applied to the resume"
    )
    skipped_suggestions: list[SkippedSuggestion] = Field(
        description="Suggestions that could not be applied without fabricating information"
    )

class SkillMatch(BaseModel):
    matched: list[str] = Field(
        default_factory=list,
        description="Skills explicitly present in both resume and JD"
    )
    missing_critical: list[str] = Field(
        default_factory=list,
        description="Skills marked required in JD but absent from resume"
    )
    missing_good_to_have: list[str] = Field(
        default_factory=list,
        description="Skills marked preferred/nice-to-have in JD but absent from resume"
    )

class SectionScore(BaseModel):
    score: int = Field(description="Score for this section out of 10")
    reason: str = Field(description="One paragraph explaining this score")

class ImprovementSuggestion(BaseModel):
    section: str = Field(description="Which section this suggestion targets e.g. 'Skills', 'Work Experience', 'Summary'")
    suggestion: str = Field(description="Concrete, actionable suggestion — not vague advice")
    priority: str = Field(description="'high', 'medium', or 'low'")

class ResumeScore(BaseModel):
    overall_score: int = Field(description="Overall match score out of 100")
    verdict: str = Field(description="One sentence executive summary of the match")

    skills_score: SectionScore
    experience_score: SectionScore
    education_score: SectionScore
    projects_score: SectionScore

    skill_match: SkillMatch

    strengths: list[str] = Field(
        description="Top 3-5 things this resume does well against this JD"
    )
    improvements: list[ImprovementSuggestion] = Field(
        description="Ordered high-priority first"
    )

    rewritten_summary: Optional[str] = Field(
        default=None,
        description="A rewritten profile summary tailored to this JD using the candidate's actual experience. null if no improvement is possible."
    )
