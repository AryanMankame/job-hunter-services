from pydantic import BaseModel, Field
from typing import Optional

class WorkExperience(BaseModel):
    company: str = Field(description="Company or organization name")
    role: str = Field(description="Job title or role")
    duration: Optional[str] = Field(description="Employment period, e.g. 'Jan 2022 - Mar 2024'")
    duration_months: Optional[int] = Field(description="Approximate total months in this role, inferred from duration")
    responsibilities: list[str] = Field(description="List of responsibilities or achievements, each as a standalone sentence")
    is_current: bool = Field(default=False, description="True if this is the person's current job")

class Education(BaseModel):
    institution: str
    degree: Optional[str] = Field(description="e.g. 'B.Tech Computer Science'")
    graduation_year: Optional[int] = None
    cgpa_or_percentage: Optional[str] = None

class Project(BaseModel):
    name: str
    description: str = Field(description="What the project does, in 1-2 sentences")
    tech_stack: list[str] = Field(default_factory=list, description="Technologies used")

class ResumeData(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    location: Optional[str] = Field(description="City, State or Country if mentioned")
    
    summary: Optional[str] = Field(description="Profile summary or objective, if present")
    
    skills: list[str] = Field(
        description="Flat list of all technical and non-technical skills. Deduplicated."
    )
    
    work_experience: list[WorkExperience] = Field(
        description="Ordered newest-first"
    )
    education: list[Education]
    projects: list[Project] = Field(default_factory=list)
    
    certifications: list[str] = Field(default_factory=list)
    languages_spoken: list[str] = Field(default_factory=list)
    
    total_experience_months: Optional[int] = Field(
        description="Sum of all work experience durations in months, excluding overlaps if detectable"
    )
