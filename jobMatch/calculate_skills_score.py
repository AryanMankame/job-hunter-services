import re
from typing import List, Dict, Set, Tuple

class SkillsMatcher:
    
    SKILL_NORMALIZATION = {
        # JavaScript variants
        "javascript": ["javascript", "javascript (es6+)", "js", "ecmascript", "es6"],
        "typescript": ["typescript", "ts"],
        
        # Frontend frameworks
        "react": ["react", "react.js", "reactjs"],
        "nextjs": ["next", "nextjs", "next.js"],
        "angular": ["angular", "angularjs"],
        "vue": ["vue", "vue.js", "vuejs"],
        "threejs": ["three", "threejs", "three.js"],
        "redux": ["redux", "redux.js", "reduxjs"],
        
        # Backend frameworks
        "express": ["express", "express.js", "expressjs"],
        "node": ["node", "node.js", "nodejs"],
        "spring": ["spring", "spring framework"],
        "springboot": ["spring boot", "springboot"],
        
        # Databases
        "postgresql": ["postgresql", "postgres", "pg"],
        "mongodb": ["mongodb", "mongo"],
        "mongoose": ["mongoose"],
        "redis": ["redis"],
        "oracle": ["oracle", "oracle db", "oracledb"],
        
        # Other tools
        "docker": ["docker"],
        "git": ["git", "github", "gitlab", "bitbucket"],
        "ci/cd": ["ci/cd", "cicd", "continuous integration", "teamcity"],
        
        # Cloud
        "aws": ["aws", "amazon web services"],
        "azure": ["azure", "microsoft azure"],
        "gcp": ["gcp", "google cloud", "google cloud platform"],
        
        # Languages
        "java": ["java"],
        "python": ["python"],
        "scala": ["scala"],
        "go": ["go", "golang"],
        "r": ["r", "r language"],
        "cpp": ["c++", "c plus plus"],
        "bash": ["bash", "bash scripting"],
        
        # Frontend
        "html": ["html", "html5"],
        "css": ["css", "css3"],
        "tailwind": ["tailwind", "tailwind css", "tailwindcss"],
        
        # Data/ML
        "tensorflow": ["tensorflow"],
        "machinelearning": ["machine learning", "ml", "machine-learning"],
        "deeplearning": ["deep learning"],
        "llm": ["llm", "large language model"],
        "rag": ["rag", "retrieval augmented generation"],
        
        # SQL
        "sql": ["sql"],
        
        # Other
        "linux": ["linux", "unix", "linux/unix"],
        "agile": ["agile", "agile methodology"],
        "scrum": ["scrum", "scrum framework"],
        "postman": ["postman", "api testing"],
    }
    
    def normalize_skill(self, skill: str) -> str:
        """Convert skill to canonical form"""
        skill_lower = skill.lower().strip()
        
        # Remove file extensions
        skill_lower = skill_lower.replace(".js", "")
        skill_lower = skill_lower.replace(".py", "")
        skill_lower = skill_lower.replace("/unix", "")
        
        # Remove parentheses and versions
        skill_lower = re.sub(r'\s*\([^)]*\)', '', skill_lower)
        skill_lower = skill_lower.strip()
        
        # Look up in mapping
        for canonical, variations in self.SKILL_NORMALIZATION.items():
            if skill_lower in variations:
                return canonical
        
        return skill_lower
    
    def calculate_skills_score(
        self,
        user_skills: List[str],
        job_required: List[str],
        job_nice_to_have: List[str] = None
    ) -> Dict:
        """
        Calculate skills match score.
        
        Returns dictionary with:
        - skills_score: overall score (0.0-1.0)
        - required_match_ratio: % of required skills matched
        - matched_required: list of skills user has
        - unmatched_required: list of skills user doesn't have
        - matched_nice_to_have: nice-to-have skills user has
        """
        
        if job_nice_to_have is None:
            job_nice_to_have = []
        
        # Normalize
        user_normalized = set([self.normalize_skill(s) for s in user_skills])
        required_normalized = set([self.normalize_skill(s) for s in job_required])
        nice_normalized = set([self.normalize_skill(s) for s in job_nice_to_have])
        
        # Required skills match
        if required_normalized:
            matched_required = user_normalized & required_normalized
            unmatched_required = required_normalized - user_normalized
            required_ratio = len(matched_required) / len(required_normalized)
        else:
            required_ratio = 1.0
            matched_required = set()
            unmatched_required = set()
        
        # Nice-to-have bonus
        nice_bonus = 0.0
        matched_nice = set()
        
        if nice_normalized:
            matched_nice = user_normalized & nice_normalized
            nice_ratio = len(matched_nice) / len(nice_normalized)
            nice_bonus = min(nice_ratio * 0.20, 0.20)  # Max 20% bonus
        
        # Final score
        skills_score = min(1.0, required_ratio + nice_bonus)
        
        return {
            "skills_score": round(skills_score, 2),
            "required_match_ratio": round(required_ratio, 2),
            "total_required": len(required_normalized),
            "total_matched_required": len(matched_required),
            "matched_required": list(matched_required),
            "unmatched_required": list(unmatched_required),
            "matched_nice_to_have": list(matched_nice),
            "unmatched_nice_to_have": list(nice_normalized - matched_nice),
        }