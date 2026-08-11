import json
import logging
import os
import uuid
from pathlib import Path

import dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from typing import Optional

from common.models import ResumeData
from models import ResumeScore, ResumeUpdateResult
from prompts import resume_update_system_prompt, resume_scoring_system_prompt

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

WORKER_DIR = Path(__file__).parent
TEMPLATE_PATH = WORKER_DIR / "resume_template.html"


def _llm_client():
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        model=os.getenv("RESUME_GENERATION_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=1,
        max_tokens=8192,
    )


def score_resume(resume_data: ResumeData, job_description: str) -> ResumeScore:
    client = _llm_client()
    parser = PydanticOutputParser(pydantic_object=ResumeScore)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", resume_scoring_system_prompt),
            (
                "human",
                """
        You will receive two inputs:
        1. A structured JSON object respresenting a parsed resume {resume_data}
        2. A raw job description in text form {job_description}
        Please evaluate the resume against the job description and return a JSON object following these instructions: {parsed_instructions}
    """,
            ),
        ]
    )
    prompt = prompt.partial(parsed_instructions=parser.get_format_instructions())
    chain = prompt | client | parser
    return chain.invoke(
        {
            "resume_data": resume_data.model_dump(mode="json"),
            "job_description": job_description,
        }
    )


def update_resume(
    resume_data: ResumeData, resume_score: ResumeScore, job_description: str
) -> ResumeUpdateResult:
    client = _llm_client()
    parser = PydanticOutputParser(pydantic_object=ResumeUpdateResult)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", resume_update_system_prompt),
            (
                "human",
                """
        I am giving you the resume data and suggestions to make changes on it to suite the job description that will also be provided:
        Resume Data: {resume_data}
        Score and Suggestions: {resume_score}
        {format_instructions}
    """,
            ),
        ]
    )
    prompt = prompt.partial(format_instructions=parser.get_format_instructions())
    chain = prompt | client | parser
    return chain.invoke(
        {
            "resume_data": resume_data.model_dump(mode="json"),
            "resume_score": resume_score.model_dump(mode="json"),
            "job_description": job_description,
        }
    )


def render_resume_to_pdf(
    resume: ResumeData,
    output_path: str = "/tmp/resume_output.pdf",
    template_path: str = str(TEMPLATE_PATH),
) -> str:
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML

    template_dir = Path(template_path).parent
    template_file = Path(template_path).name

    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template(template_file)

    html_content = template.render(resume=resume)
    HTML(string=html_content).write_pdf(output_path)
    return output_path


def upload_to_supabase(pdf_path: str, email: str) -> str:
    from supabase import create_client

    supabase = create_client(
        os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    )
    bucket = os.getenv("SUPABASE_BUCKET")
    storage_path = f"resumes/{email}/{uuid.uuid4()}.pdf"

    with open(pdf_path, "rb") as f:
        supabase.storage.from_(bucket).upload(
            path=storage_path,
            file=f,
            file_options={"content-type": "application/pdf"},
        )
    return supabase.storage.from_(bucket).get_public_url(storage_path)


def notify_orchestration(payload: dict) -> None:
    import httpx

    callback_url = os.getenv("ORCHESTRATION_CALLBACK_URL")
    if not callback_url:
        logger.error(
            "ORCHESTRATION_CALLBACK_URL not set; callback NOT sent for record %s",
            payload.get("record_id"),
        )
        return
    headers = {}
    if os.getenv("CALLBACK_SECRET"):
        headers["x-callback-secret"] = os.getenv("CALLBACK_SECRET")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{callback_url.rstrip('/')}/generateResume/callback",
            json=payload,
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.error(
                "Callback for record %s returned HTTP %s: %s",
                payload.get("record_id"),
                resp.status_code,
                resp.text[:300],
            )
            resp.raise_for_status()


def run_pipeline(resume_data: ResumeData, job_description: str):
    resume_score = score_resume(resume_data, job_description)
    update_result = update_resume(resume_data, resume_score, job_description)
    return resume_score, update_result


def _extract_payload(event: dict) -> dict:
    if isinstance(event.get("body"), str):
        return json.loads(event["body"])
    return event


def handler(event: dict, context) -> dict:
    payload = _extract_payload(event)
    email = payload["email"]
    job_id = payload["job_id"]
    record_id = payload["record_id"]
    job_description = payload["job_description"]
    resume_data = ResumeData.model_validate(payload["resume_data"])

    try:
        resume_score, update_result = run_pipeline(resume_data, job_description)

        pdf_path = render_resume_to_pdf(update_result.updated_resume)
        public_url = upload_to_supabase(pdf_path, email)

        notify_orchestration(
            {
                "record_id": record_id,
                "status": "completed",
                "url": public_url,
                "updated_resume": update_result.updated_resume.model_dump(mode="json"),
            }
        )
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "record_id": record_id,
                    "status": "completed",
                    "url": public_url,
                }
            ),
        }
    except Exception as err:
        notify_orchestration(
            {
                "record_id": record_id,
                "status": "failed",
                "error": str(err),
            }
        )
        raise


if __name__ == "__main__":
    resume_data = ResumeData.model_validate(
        {
            "full_name": "Aryan Mankame",
            "email": "aryan672002@gmail.com",
            "phone": "+91-7387159818",
            "linkedin_url": "linkedin.com/in/aryan-mankame",
            "github_url": "github.com/aryan672002",
            "location": "Pune, India",
            "summary": None,
            "skills": [
                "JavaScript (ES6+)",
                "TypeScript",
                "Java",
                "Python",
                "SQL",
                "C++",
                "Scala",
                "Bash Scripting",
                "React.js",
                "Next.js",
                "Three.js",
                "Redux.js",
                "HTML5",
                "CSS3",
                "Tailwind CSS",
                "Node.js",
                "Express.js",
                "Oracle DB",
                "PostgreSQL",
                "MongoDB",
                "Mongoose",
                "Tensorflow",
                "Machine Learning Algorithms",
                "RAG",
                "Deep Learning",
                "LLM",
                "Docker",
                "Git",
                "Bitbucket API",
                "TeamCity",
                "CI/CD",
                "Linux/Unix",
                "Postman",
            ],
            "work_experience": [
                {
                    "company": "Deutsche Bank",
                    "role": "Software Development Engineer",
                    "duration": "Jul 2024 – Present",
                    "duration_months": 25,
                    "responsibilities": [
                        "Implemented Oracle database full backups with hourly mirroring, increasing system reliability and limiting outage downtime to under 5 minutes in case of failures.",
                        "Developed an FAQ chatbot to automate user support, reducing daily Q&A tickets by 90% (from about 10 per day to 1 per day).",
                        "Refactored the RFTA platform's architecture by consolidating five database-utilizing services into a single shared service, reducing downtime for database-connection updates from 30 minutes to 3 minutes.",
                        "Optimized the report-download service, tripling its download speed through performance tuning, which improved throughput by 3x without adding hardware.",
                        "Built a Scala-based data-connection management module (backed by a web interface) that automates credential updates via the Bitbucket API. This reduced user update time from 10–15 minutes down to about 10–30 seconds.",
                        "Developed a regression test suite for the Python 'ltb-bd' build-deployment package, boosting its unit-test coverage from 60% to 90% and catching more bugs before release.",
                        "Configured and maintained TeamCity CI/CD pipelines to automate builds and deployments across projects (ensuring consistent continuous integration).",
                    ],
                    "is_current": True,
                }
            ],
            "education": [
                {
                    "institution": "Maulana Azad National Institute of Technology Bhopal",
                    "degree": "Bachelor of Technology in Computer Science",
                    "graduation_year": 2024,
                    "cgpa_or_percentage": "CGPA: 8.98 / 10.0",
                }
            ],
            "projects": [
                {
                    "name": "Mongo-Bolt",
                    "description": "Designed and published a lightweight TypeScript NPM package to simplify MongoDB operations, allowing developers to perform MongoDB operations with concise, reusable code.",
                    "tech_stack": ["TypeScript", "Node.js", "MongoDB"],
                },
                {
                    "name": "FitTrackMe",
                    "description": "Built a comprehensive fitness platform featuring modules like Meal Planner, Exercise Tracker, and a GPT-3 powered Health Assistant, achieving near-perfect web performance scores.",
                    "tech_stack": ["React.js", "Express.js", "Redux", "OpenAI API"],
                },
                {
                    "name": "EHM-Cervix",
                    "description": "Combined three hybrid CNN architectures to classify cervical cancer images from the SIPakMed dataset, generating a classification accuracy of 95.10%.",
                    "tech_stack": ["Python", "CNN", "TensorFlow"],
                },
            ],
            "certifications": [],
            "languages_spoken": [],
            "total_experience_months": 25,
        }
    )

    job_description = """Join us as a Software Engineer

• This is an opportunity for a driven Software Engineer to take on an exciting new career challenge
• Day-to-day, you'll build a wide network of stakeholders of varying levels of seniority
• It's a chance to hone your existing technical skills and advance your career
• We're offering this role at associate vice president level

What you'll do

In your new role, you'll engineer and maintain innovative, customer centric, high performance, secure and robust solutions. You'll be working within a feature team and using your extensive experience to engineer software, scripts and tools that are often complex, as well as liaising with other engineers, architects and business analysts across the platform.

You'll also be:

• Producing complex and critical software rapidly and of high quality which adds value to the business
• Working in permanent teams who are responsible for the full life cycle, from initial development, through enhancement and maintenance to replacement or decommissioning
• Collaborating to optimise our software engineering capability
• Designing, producing, testing and implementing our working code
• Working across the life cycle, from requirements analysis and design, through coding to testing, deployment and operations

The skills you'll need

You'll need at least eight years of software engineering, software design, architecture, and an understanding of how your area of expertise supports our customers.

You'll also need:

• Experience of working with development and testing tools, bug tracking tools and wikis
• Experience in C#, Azure, TDD and ReactJS
• Experience of DevOps, Testing and Agile methodology and associated toolsets
• A background in solving highly complex, analytical and numerical problems
• Experience of implementing programming best practice, especially around scalability, automation, virtualisation, optimisation, availability and performance"""

    resume_score, update_result = run_pipeline(resume_data, job_description)
    print("SCORE => ", resume_score)
    print("OUTPUT => ", update_result)
    output_path = render_resume_to_pdf(update_result.updated_resume)
    print(f"PDF written to: {output_path}")
