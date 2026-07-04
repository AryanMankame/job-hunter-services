import re
from langchain_openai import ChatOpenAI
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
import dotenv
import os
from datetime import date
dotenv.load_dotenv()
from pydantic import BaseModel, Field
from typing import Optional
from helpers import verify_correct_email_format

import logging
import time
from langchain_core.callbacks import BaseCallbackHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resume_parser")

class ModelTraceCallback(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        self._start_time = time.time()
        # invocation_params has the actual model name being called
        model_name = kwargs.get("invocation_params", {}).get("model", "unknown")
        self._current_model = model_name
        logger.info(f"[TRACE] Attempting model: {model_name}")

    def on_llm_end(self, response, **kwargs):
        elapsed = time.time() - self._start_time
        logger.info(f"[TRACE] Model {self._current_model} succeeded in {elapsed:.2f}s")

    def on_llm_error(self, error, **kwargs):
        elapsed = time.time() - self._start_time
        logger.warning(f"[TRACE] Model {self._current_model} FAILED after {elapsed:.2f}s: {error}")

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

class ResumeDataParser:
    def __init__(self):
        self.__resume_parsing_system_prompt = """
        You are a precise resume parsing engine. Your only job is to extract structured 
        information from raw resume text and return it as valid JSON matching the schema 
        provided. You must never hallucinate, guess, or invent data not present in the text.

        ## INPUT CHARACTERISTICS — READ THIS FIRST

        The text you receive has been extracted from a PDF using PyPDF. This means:
        - Layout information is destroyed. A two-column resume will have text from both 
        columns interleaved in unpredictable ways.
        - Section headers may appear mid-sentence or be concatenated with adjacent content.
        - Bullet points lose their symbols and may appear as run-on text.
        - Tables become space-separated tokens with no clear row/column boundaries.
        - Dates may appear before or after the company/role they belong to.

        Do not treat the text as having clean paragraph or section structure. 
        Read it holistically and infer meaning from context and keywords.

        ## EXTRACTION RULES

        ### Identity & Contact
        - Extract the person's name from the first recognizable person name in the text.
        It usually appears at the very top but may be embedded if the header was a table.
        - Email: match pattern \S+@\S+\.\S+
        - Phone: any digit sequence of 10+ digits, with or without country code and separators
        - LinkedIn: any URL containing "linkedin.com/in/"
        - GitHub: any URL containing "github.com/" followed by a username (not "github.com/features" etc.)

        ### Work Experience
        - Identify entries by the presence of a company name + role title pattern, 
        usually near a date range.
        - Date formats vary wildly: "Jan 2022", "01/2022", "January, 2022", "2022", 
        "Present", "Current", "Till date" — normalize these to human-readable strings, 
        do not reformat them.
        - Compute duration_months by converting start/end to month counts. 
        If only years are given (e.g. "2021 - 2023"), assume 12 months per year.
        If end date is "Present" or "Current", use the todays date {today_date}.
        - Set is_current: true only if the end date is explicitly "Present", "Current", 
        "Till date", or equivalent.
        - Responsibilities: split on implicit bullet points — look for repeated sentence 
        starts, action verbs at the start of clauses, or line breaks encoded as extra 
        spaces. Each responsibility should be one complete thought.
        - If two jobs appear to be at the same company with different roles 
        (e.g. promoted), treat them as separate WorkExperience entries.

        ### Skills
        - Collect all skills from dedicated skills sections AND from within 
        responsibilities and project descriptions.
        - Deduplicate case-insensitively: "Python", "python", "PYTHON" → "Python"
        - Include both technical (languages, frameworks, tools) and soft skills 
        if explicitly listed.
        - Do not infer skills. If a responsibility says "built REST APIs", 
        do not add "REST API" to skills unless explicitly listed somewhere.

        ### Education
        - Match: institution name + degree + year pattern
        - graduation_year: extract the year the degree was awarded, 
        not the start year. If only a range is given (e.g. "2018-2022"), use 2022.
        - cgpa_or_percentage: preserve the raw string including the scale 
        ("8.4/10", "74%", "8.4 CGPA")

        ### Projects
        - Look for sections titled "Projects", "Personal Projects", "Academic Projects", 
        "Side Projects", or similar.
        - A project entry usually has a name (often bold/title-cased or on its own line) 
        followed by a description.
        - Extract tech_stack from within the description — look for parenthetical 
        tech mentions like "(React, Node.js, MongoDB)" or "Built using Python and FastAPI"
        - If the project description is a run-on due to PDF extraction, split it 
        into a coherent 1-2 sentence summary.

        ### Total Experience
        - Sum duration_months across all work_experience entries.
        - If date ranges overlap (e.g. a person freelanced while employed), 
        do not double count — use the outer span of the overlapping period.

        ## OUTPUT RULES

        1. Return ONLY the JSON object. No explanation, no markdown fences, 
        no preamble, no "Here is the parsed resume:".
        2. For any field where the information is genuinely absent, use null 
        for Optional fields and [] for list fields. Never use placeholder 
        strings like "N/A" or "Not mentioned".
        3. If you encounter two plausible values for the same field (e.g. two emails), 
        pick the one that appears more prominently or first, and discard the other.
        4. Do not fix typos in the candidate's content. Extract as-is 
        (except for skills deduplication).
        5. The output must be valid JSON parseable by Python's json.loads(). 
        No trailing commas, no comments.
        """
        self.__client = ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            model="gpt-4o-mini",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            temperature=1,
            max_tokens=8192,
        )
        self.__fallback_llm = ChatNVIDIA(
            model="minimaxai/minimax-m3",
            api_key=os.getenv("NVIDIA_API_KEY"),
            temperature=1,
            top_p=0.95,
            max_completion_tokens=8192,
            timeout=30
        )
        self.__client = self.__client.with_fallbacks([self.__fallback_llm])
        self.__parser = PydanticOutputParser(pydantic_object=ResumeData)

        self.__prompt = ChatPromptTemplate.from_messages([
            ("system", self.__resume_parsing_system_prompt),
            ("human", """
                Extract the information from the text below:
                {resume_text}
                Follow the these format instructions:
                {format_instructions}
            """)
        ])
        self.__prompt = self.__prompt.partial(format_instructions=self.__parser.get_format_instructions())
        self.__chain = self.__prompt | self.__client | self.__parser
    def parse(self,resume_text: str) -> ResumeData:
        trace_callback = ModelTraceCallback()
        return self.__chain.invoke({"resume_text" : resume_text,"today_date" : date.today().isoformat()}, config={"callbacks": [trace_callback]})



if __name__ == "__main__":
    parser = ResumeDataParser()
    with open("/Users/aryanmankame/Projects/jobHunter/POC/AryanMankameResume.pdf", "rb") as f:
        from pypdf import PdfReader
        reader = PdfReader(f)
        resume_text = ""
        for page in reader.pages:
            resume_text += page.extract_text() + "\n"
    resume_data = parser.parse(resume_text)
    print(resume_data.model_dump_json(indent=2))