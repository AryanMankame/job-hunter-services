import boto3
import requests
from requests_aws4auth import AWS4Auth
import os
from dotenv import load_dotenv
load_dotenv()
REGION = "us-east-1"
WORKER_FUNCTION_URL = "https://2x3iuhz25ypelfvcple45ka7oe0rzyka.lambda-url.us-east-1.on.aws"
res_data = {
  "full_name": "Aryan Mankame",
  "email": "aryan672002@gmail.com",
  "phone": "+91-7387159818",
  "linkedin_url": "linkedin.com/in/aryan-mankame",
  "github_url": "github.com/aryan672002",
  "location": "Pune, India",
  "summary": "null",
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
    "Docker",
    "Git",
    "Bitbucket API",
    "TeamCity",
    "CI/CD",
    "Linux/Unix",
    "Postman"
  ],
  "work_experience": [
    {
      "company": "Deutsche Bank",
      "role": "Senior Analyst",
      "duration": "Jul 2024 – Present",
      "duration_months": 26,
      "responsibilities": [
        "Developed a React-based dashboard to visualize repository data.",
        "Implemented backend controllers to prefetch connection details and automated Pull Requests (PRs) using the Bitbucket API, significantly reducing manual configuration time.",
        "Architected an automated hourly backup and recovery system using Oracle Import/Export (imp/exp) utilities.",
        "Established a real-time mirror database mechanism to ensure zero data loss and high availability.",
        "Re-engineered deployment workflows on TeamCity by configuring 4+ agents and migrating 20+ jobs.",
        "Reduced build queue times by 60% and improved deployment reliability.",
        "Optimized a Scala-based reporting service, achieving a 10x improvement in processing speed.",
        "Enhanced Python testbench reliability by increasing unit test coverage from 60% to 90%.",
        "Engineered a 'Local Mode' for testbench execution, decoupling the testing process from remote dependencies and accelerating the development feedback loop."
      ],
      "is_current": True
    }
  ],
  "education": [
    {
      "institution": "Maulana Azad National Institute of Technology (MANIT)",
      "degree": "Bachelor of Technology in Computer Science",
      "graduation_year": 2024,
      "cgpa_or_percentage": "CGPA: 8.98 / 10.0"
    }
  ],
  "projects": [
    {
      "name": "Mongo-Bolt",
      "description": "Designed and published a lightweight TypeScript NPM package to simplify MongoDB operations. Implemented an intuitive API featuring inbuilt caching, simplified joins, and a fluent aggregation builder. Achieved 200+ weekly downloads, creating strong adoption across the developer community.",
      "tech_stack": [
        "TypeScript",
        "Node.js",
        "MongoDB"
      ]
    },
    {
      "name": "FitTrackMe",
      "description": "Built a comprehensive fitness platform featuring modules like Meal Planner, Exercise Tracker, and a GPT-3 powered Health Assistant. Implemented Exercise Tracker for recording and tracking daily physical activity. Integrated GPT-3 to deliver real-time responses to health-related queries, simulating a virtual assistant. Achieved near-perfect web performance scores: Performance: 99 — Accessibility: 94 — Best Practices: 100 — SEO: 100.",
      "tech_stack": [
        "React.js",
        "Express.js",
        "Redux",
        "OpenAI API"
      ]
    },
    {
      "name": "EHM-Cervix",
      "description": "Combined three hybrid CNN architectures to classify cervical cancer images from the SIPakMed dataset. Achieved a classification accuracy of 95.10%, outperforming previous CNN-based benchmarks. Demonstrated advanced use of ensemble modeling and feature extraction for medical imaging.",
      "tech_stack": [
        "Python",
        "CNN",
        "TensorFlow"
      ]
    }
  ],
  "certifications": [],
  "languages_spoken": [],
  "total_experience_months": 26
}
payload = {
    "record_id" : "6a6f1f903cf3a93f554300d5",
    "email" : "abcd@gmail.com",
    "job_id" : "U90y6SvcChqe_l-LAAAAAA==",
    "resume_data" : res_data,
    "job_description" : "About the job: We are looking for a Computer Science/IT graduate or equivalent self-taught candidate based in Bangalore to work on client and in-house projects. The role involves implementing and customizing open-source platforms such as Frappe/ERPNext and Medusa.js, building integrations using REST APIs, webhooks, and n8n, and supporting in-house products such as Polygin and Smartflo. You will debug full-stack issues, assist with deployment, hosting, maintenance, and server management, write clean and well-documented code, participate in code reviews, and contribute to internal tools and automation initiatives alongside senior engineers. No prior experience is required. Candidates should have basic knowledge of Python, JavaScript, REST APIs, Git, Node.js, and MySQL, with familiarity with the Frappe Framework being valuable. A GitHub profile or portfolio showcasing a personal project is a strong plus. The position offers ₹3,00,000–₹4,50,000 per year, with an informal dress code. The application deadline is August 8, 2026, at 11:59:59 PM. Polemarch appears to be a specialized financial services company operating in India's unlisted securities market."
}
print("access key => ",os.getenv("AWS_ACCESS_KEY_ID"), os.getenv("AWS_SECRET_ACCESS_KEY"))
session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)
print(session.get_credentials().access_key)

credentials = session.get_credentials().get_frozen_credentials()

auth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    REGION,
    "lambda",
    session_token=credentials.token,
)


response = requests.post(
                f"{WORKER_FUNCTION_URL}/",
                json=payload,
                auth=auth,
                timeout=320,
            )
print(response)
print("Status:", response.status_code)
print("Body:", response.text)