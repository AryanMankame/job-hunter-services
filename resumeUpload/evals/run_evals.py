"""
Evaluate resume parsing quality against golden expected outputs.

Scores each resume across identity, skills, work experience, education,
projects, and total experience sections with fuzzy matching for text fields,
set-based F1 for skills/tech-stack, and numeric tolerance for month counts.
Produces a weighted overall score and logs results for trend tracking.

Usage:
    cd <server_root>
    python -m resumeUpload.evals.run_evals             # runs all golden resumes
    python -m resumeUpload.evals.run_evals --resume 2  # runs specific resume (number only)
    python -m resumeUpload.evals.run_evals --verbose   # prints per-resume details
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from difflib import SequenceMatcher

import pandas as pd
from dataclasses import dataclass, asdict, field

from resumeUpload.ResumeDataParser import ResumeDataParser
from pypdf import PdfReader


# ─── Fuzzy helpers ────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.strip().lower()


def _tokenize(s: str) -> set[str]:
    return set(_norm(s).split())


def _fuzzy(a: str | None, b: str | None) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _token_f1(a: str | None, b: str | None) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa, sb = _tokenize(a), _tokenize(b)
    if not sa and not sb:
        return 1.0
    inter = sa & sb
    p = len(inter) / len(sb) if sb else 1.0
    r = len(inter) / len(sa) if sa else 1.0
    return 2 * p * r / (p + r) if p + r > 0 else 0.0


def _set_f1(actual: list[str], expected: list[str]) -> float:
    a = {_norm(s) for s in actual}
    e = {_norm(s) for s in expected}
    if not a and not e:
        return 1.0
    inter = a & e
    p = len(inter) / len(a) if a else 1.0
    r = len(inter) / len(e) if e else 1.0
    return 2 * p * r / (p + r) if p + r > 0 else 0.0


def _iso_list(values: list | None) -> list:
    return values if isinstance(values, list) else []


def _ellide(s: str, n: int = 50) -> str:
    return s if len(s) <= n else s[:n] + "…"


# ─── Pairing (greedy best-match) ──────────────────────────────────

def _pair_entries(
    actual: list[dict],
    expected: list[dict],
    key_fn,
    sim_fn=_fuzzy,
    sim_threshold: float = 0.5,
):
    """
    Greedily pair actual → expected entries using similarity *key_fn*.
    Returns (paired, unmatched_actual, unmatched_expected).
    Each element in *paired* is (actual_idx, expected_idx, similarity).
    """
    paired: list[tuple[int, int, float]] = []
    used_actual: set[int] = set()
    used_expected: set[int] = set()

    scores: list[list[float]] = [
        [sim_fn(key_fn(a), key_fn(e)) for e in expected] for a in actual
    ]

    changed = True
    while changed:
        changed = False
        best_i = best_j = -1
        best_s = sim_threshold
        for i, row in enumerate(scores):
            if i in used_actual:
                continue
            for j, s in enumerate(row):
                if j in used_expected:
                    continue
                if s > best_s:
                    best_s = s
                    best_i, best_j = i, j
                    changed = True
        if best_i >= 0:
            paired.append((best_i, best_j, best_s))
            used_actual.add(best_i)
            used_expected.add(best_j)

    unmatched_actual = [i for i in range(len(actual)) if i not in used_actual]
    unmatched_expected = [j for j in range(len(expected)) if j not in used_expected]
    return paired, unmatched_actual, unmatched_expected


# ─── Section scorers ──────────────────────────────────────────────

def _score_identity(actual: dict, expected: dict, verbose: bool) -> tuple[float, dict]:
    fields = {
        "full_name":    (_fuzzy, 0.30),
        "email":        (_token_f1, 0.20),
        "phone":        (_fuzzy, 0.20),
        "location":     (_fuzzy, 0.15),
        "linkedin_url": (_fuzzy, 0.075),
        "github_url":   (_fuzzy, 0.075),
    }
    scores: dict[str, float] = {}
    total = 0.0
    weight_sum = 0.0
    for key, (fn, w) in fields.items():
        a, e = actual.get(key), expected.get(key)
        if e is None or (isinstance(e, str) and not e.strip()):
            continue  # not applicable
        s = fn(a, e)
        scores[key] = s
        total += s * w
        weight_sum += w
        if verbose:
            a_str = str(a or "")
            e_str = str(e or "")
            print(f"    {key}: {_ellide(a_str)} vs {_ellide(e_str)} → {s:.2f}")
    if weight_sum == 0:
        return 100.0, scores
    return (total / weight_sum) * 100, scores


def _score_skills(actual: list[str], expected: list[str]) -> tuple[float, float, float, float]:
    p = r = f1 = 0.0
    a = {_norm(s) for s in actual}
    e = {_norm(s) for s in expected}
    inter = a & e
    r = len(inter) / len(e) if e else 1.0
    p = len(inter) / len(a) if a else 1.0
    f1 = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return f1 * 100, r, p, f1


def _score_work_experience(actual: list[dict], expected: list[dict], verbose: bool) -> tuple[float, list[str]]:
    hallucinations: list[str] = []

    if not expected:
        # No work exp expected; score 100 if actual is also empty, else penalise
        if not actual:
            return 100.0, []
        score = max(0.0, 100.0 - len(actual) * 25)
        hallucinations.append(f"unexpected work exp entries: {len(actual)}")
        return score, hallucinations

    def _we_key(e: dict) -> str:
        return f"{e.get('company','')} {e.get('role','')}"

    paired, unmatched_actual, unmatched_expected = _pair_entries(actual, expected, _we_key)

    total = 0.0
    count = 0

    for ai, ei, sim in paired:
        a = actual[ai]
        e = expected[ei]
        s = 0.0
        # company (fuzzy) 0.25
        s += _fuzzy(a.get("company"), e.get("company")) * 0.25
        # role (fuzzy) 0.25
        s += _fuzzy(a.get("role"), e.get("role")) * 0.25
        # duration (fuzzy) 0.15
        s += _fuzzy(a.get("duration"), e.get("duration")) * 0.15
        # duration_months (tolerance) 0.10
        am = a.get("duration_months")
        em = e.get("duration_months")
        if em is not None and am is not None:
            s += (1.0 if abs(am - em) <= 2 else 0.0) * 0.10
        elif em is None and am is None:
            s += 0.10
        # responsibilities (set F1) 0.20
        s += _set_f1(_iso_list(a.get("responsibilities")), _iso_list(e.get("responsibilities"))) * 0.20
        # is_current (exact) 0.05
        s += (1.0 if a.get("is_current") == e.get("is_current") else 0.0) * 0.05

        total += s
        count += 1
        if verbose:
            print(f"    Work: {_ellide(e.get('company',''))} / {_ellide(e.get('role',''))} → {s:.2f}")

    for i in unmatched_expected:
        total += 0.0
        count += 1
        if verbose:
            print(f"    Work: MISSED entry {expected[i].get('company','')} / {expected[i].get('role','')}")
    for i in unmatched_actual:
        hallucinations.append(f"unexpected work: {actual[i].get('company','')} / {actual[i].get('role','')}")

    score = (total / count * 100) if count else 100.0
    return score, hallucinations


def _score_education(actual: list[dict], expected: list[dict], verbose: bool) -> tuple[float, list[str]]:
    hallucinations: list[str] = []

    if not expected:
        if not actual:
            return 100.0, []
        score = max(0.0, 100.0 - len(actual) * 25)
        hallucinations.append(f"unexpected education entries: {len(actual)}")
        return score, hallucinations

    def _ed_key(e: dict) -> str:
        return e.get("institution", "")

    paired, unmatched_actual, unmatched_expected = _pair_entries(actual, expected, _ed_key)

    total = 0.0
    count = 0

    for ai, ei, sim in paired:
        a = actual[ai]
        e = expected[ei]
        s = 0.0
        # institution (fuzzy) 0.30
        s += _fuzzy(a.get("institution"), e.get("institution")) * 0.30
        # degree (fuzzy) 0.25
        s += _fuzzy(a.get("degree"), e.get("degree")) * 0.25
        # graduation_year 0.25
        ay = a.get("graduation_year")
        ey_val = e.get("graduation_year")
        if ey_val is not None and ay is not None:
            s += (1.0 if ay == ey_val else 0.0) * 0.25
        elif ey_val is None and ay is None:
            s += 0.25
        # cgpa_or_percentage (fuzzy) 0.20
        s += _fuzzy(a.get("cgpa_or_percentage"), e.get("cgpa_or_percentage")) * 0.20

        total += s
        count += 1
        if verbose:
            print(f"    Edu: {_ellide(e.get('institution',''))} → {s:.2f}")

    for i in unmatched_expected:
        total += 0.0
        count += 1
        if verbose:
            print(f"    Edu: MISSED {expected[i].get('institution','')}")
    for i in unmatched_actual:
        hallucinations.append(f"unexpected education: {actual[i].get('institution','')}")

    score = (total / count * 100) if count else 100.0
    return score, hallucinations


def _score_projects(actual: list[dict], expected: list[dict], verbose: bool) -> tuple[float, list[str]]:
    hallucinations: list[str] = []

    if not expected:
        if not actual:
            return 100.0, []
        score = max(0.0, 100.0 - len(actual) * 25)
        hallucinations.append(f"unexpected project entries: {len(actual)}")
        return score, hallucinations

    def _proj_key(e: dict) -> str:
        return e.get("name", "")

    paired, unmatched_actual, unmatched_expected = _pair_entries(actual, expected, _proj_key)

    total = 0.0
    count = 0

    for ai, ei, sim in paired:
        a = actual[ai]
        e = expected[ei]
        s = 0.0
        # name (fuzzy) 0.30
        s += _fuzzy(a.get("name"), e.get("name")) * 0.30
        # description (fuzzy) 0.30
        s += _fuzzy(a.get("description"), e.get("description")) * 0.30
        # tech_stack (set F1) 0.40
        s += _set_f1(_iso_list(a.get("tech_stack")), _iso_list(e.get("tech_stack"))) * 0.40

        total += s
        count += 1
        if verbose:
            print(f"    Project: {_ellide(e.get('name',''))} → {s:.2f}")

    for i in unmatched_expected:
        total += 0.0
        count += 1
        if verbose:
            print(f"    Project: MISSED {expected[i].get('name','')}")
    for i in unmatched_actual:
        hallucinations.append(f"unexpected project: {actual[i].get('name','')}")

    score = (total / count * 100) if count else 100.0
    return score, hallucinations


def _score_total_experience(actual: Optional[int], expected: Optional[int]) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    return abs(actual - expected) <= 2


# ─── Hallucination across all sections ────────────────────────────

def _is_novel(actual_val: str, expected_values: set[str], threshold: float = 0.55) -> bool:
    """Return True if *actual_val* has no close fuzzy match in *expected_values*."""
    if not actual_val.strip():
        return False
    for ev in expected_values:
        if _fuzzy(actual_val, ev) >= threshold:
            return False
    return True


def _detect_hallucinations(actual: dict, expected: dict) -> list[str]:
    issues: list[str] = []

    # Identity
    for key in ("full_name", "email", "phone", "location", "linkedin_url", "github_url"):
        a, e = actual.get(key), expected.get(key)
        if e and not a:
            issues.append(f"missing_{key}: expected present but actual absent")
        elif not e and a:
            issues.append(f"unexpected_{key}: parser returned '{a}' but expected was empty")

    # Skills (use fuzzy to avoid ligature / spacing differences)
    e_skills = {_norm(s) for s in _iso_list(expected.get("skills"))}
    for s in _iso_list(actual.get("skills")):
        if _is_novel(s, e_skills):
            issues.append(f"extra_skill: '{s}'")

    # Work experience companies (fuzzy)
    e_cos = {_norm(w.get("company", "")) for w in _iso_list(expected.get("work_experience"))}
    for w in _iso_list(actual.get("work_experience")):
        c = w.get("company", "")
        if _is_novel(c, e_cos):
            issues.append(f"extra_company: '{c}'")

    # Education institutions (fuzzy)
    e_inst = {_norm(w.get("institution", "")) for w in _iso_list(expected.get("education"))}
    for w in _iso_list(actual.get("education")):
        inst = w.get("institution", "")
        if _is_novel(inst, e_inst):
            issues.append(f"extra_institution: '{inst}'")

    # Projects (fuzzy)
    e_proj = {_norm(w.get("name", "")) for w in _iso_list(expected.get("projects"))}
    for w in _iso_list(actual.get("projects")):
        pname = w.get("name", "")
        if _is_novel(pname, e_proj):
            issues.append(f"extra_project: '{pname}'")

    return issues


# ─── ScoreResult ──────────────────────────────────────────────────

@dataclass
class ScoreResult:
    filename: str
    overall_score: float = 0.0
    identity_score: float = 0.0
    skills_f1_score: float = 0.0
    skills_recall: float = 0.0
    skills_precision: float = 0.0
    work_experience_score: float = 0.0
    education_score: float = 0.0
    projects_score: float = 0.0
    total_experience_months_match: bool = False
    hallucination_detected: bool = False
    hallucination_count: int = 0
    error: Optional[str] = None


# ─── Weights ──────────────────────────────────────────────────────

SECTION_WEIGHTS = {
    "identity": 0.15,
    "skills": 0.20,
    "work_experience": 0.30,
    "education": 0.20,
    "projects": 0.10,
    "total_experience": 0.05,
}


# ─── Evaluator ────────────────────────────────────────────────────

class ResumeEvaluator:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.parser = ResumeDataParser()

    def score_resume(self, actual: dict, expected: dict, filename: str) -> ScoreResult:
        result = ScoreResult(filename=filename)

        try:
            # ── Identity ──
            identity_score, id_details = _score_identity(actual, expected, self.verbose)
            result.identity_score = identity_score

            # ── Skills ──
            f1_score, recall, precision, _ = _score_skills(
                _iso_list(actual.get("skills")), _iso_list(expected.get("skills"))
            )
            result.skills_f1_score = f1_score
            result.skills_recall = recall
            result.skills_precision = precision

            # ── Work experience ──
            we_score, we_hallucinations = _score_work_experience(
                _iso_list(actual.get("work_experience")),
                _iso_list(expected.get("work_experience")),
                self.verbose,
            )
            result.work_experience_score = we_score

            # ── Education ──
            edu_score, edu_hallucinations = _score_education(
                _iso_list(actual.get("education")),
                _iso_list(expected.get("education")),
                self.verbose,
            )
            result.education_score = edu_score

            # ── Projects ──
            proj_score, proj_hallucinations = _score_projects(
                _iso_list(actual.get("projects")),
                _iso_list(expected.get("projects")),
                self.verbose,
            )
            result.projects_score = proj_score

            # ── Total experience ──
            result.total_experience_months_match = _score_total_experience(
                actual.get("total_experience_months"),
                expected.get("total_experience_months"),
            )

            # ── Hallucination (global scan) ──
            all_issues = we_hallucinations + edu_hallucinations + proj_hallucinations
            all_issues += _detect_hallucinations(actual, expected)
            seen = set()
            unique_issues: list[str] = []
            for issue in all_issues:
                if issue not in seen:
                    seen.add(issue)
                    unique_issues.append(issue)
            result.hallucination_detected = len(unique_issues) > 0
            result.hallucination_count = len(unique_issues)
            if self.verbose and unique_issues:
                for issue in unique_issues:
                    print(f"  ⚠️  {issue}")

            # ── Overall ──
            overall = (
                result.identity_score * SECTION_WEIGHTS["identity"]
                + result.skills_f1_score * SECTION_WEIGHTS["skills"]
                + result.work_experience_score * SECTION_WEIGHTS["work_experience"]
                + result.education_score * SECTION_WEIGHTS["education"]
                + result.projects_score * SECTION_WEIGHTS["projects"]
                + (100.0 if result.total_experience_months_match else 0.0)
                * SECTION_WEIGHTS["total_experience"]
            )
            # Hallucination penalty: -3 per hallucinated item, capped at -15
            penalty = min(result.hallucination_count * 3, 15)
            result.overall_score = max(0.0, overall - penalty)

        except Exception as e:
            result.error = str(e)
            if self.verbose:
                print(f"  ❌ Scoring error: {e}")

        return result

    def run_eval(self, resume_num: Optional[str] = None) -> list[ScoreResult]:
        golden_dir = Path(__file__).parent / "data"
        if not golden_dir.exists():
            print(f"❌ Golden resumes directory not found: {golden_dir}")
            return []

        if resume_num:
            expected_files = list(golden_dir.glob(f"resume{resume_num}.expected.json"))
        else:
            expected_files = sorted(golden_dir.glob("*.expected.json"))

        if not expected_files:
            print(f"❌ No expected.json files found in {golden_dir}")
            return []

        results = []

        for expected_path in expected_files:
            pdf_path = expected_path.parent / (expected_path.name.replace(".expected.json", ".pdf"))

            if not pdf_path.exists():
                print(f"⚠️  Skipping {expected_path.name}: PDF not found at {pdf_path}")
                continue

            print(f"\n📄 Processing {pdf_path.name}...", end=" ")

            try:
                with open(pdf_path, "rb") as f:
                    reader = PdfReader(f)
                    extracted_text = "".join(
                        page.extract_text() or "" for page in reader.pages
                    )

                if not extracted_text.strip():
                    print("❌ (empty PDF)")
                    results.append(ScoreResult(filename=expected_path.name, error="No extractable text from PDF"))
                    continue

            except Exception as e:
                print("❌ (PDF read error)")
                results.append(ScoreResult(filename=expected_path.name, error=f"PDF extraction failed: {e}"))
                continue

            try:
                actual = self.parser.parse(extracted_text).model_dump(mode="json")
            except Exception as e:
                print("❌ (parse error)")
                results.append(ScoreResult(filename=expected_path.name, error=f"LLM parsing failed: {e}"))
                continue

            expected = json.loads(expected_path.read_text())
            result = self.score_resume(actual, expected, expected_path.name)
            results.append(result)

            if result.error:
                print(f"❌ Error: {result.error}")
            else:
                print(f"✓ overall={result.overall_score:.1f}  "
                      f"id={result.identity_score:.0f}  "
                      f"skills={result.skills_f1_score:.0f}  "
                      f"work={result.work_experience_score:.0f}  "
                      f"edu={result.education_score:.0f}  "
                      f"proj={result.projects_score:.0f}  "
                      f"halluc={result.hallucination_count}")

        return results


# ─── Summary ──────────────────────────────────────────────────────

def print_summary(results: list[ScoreResult]):
    print("\n" + "=" * 70)
    print("EVALUATION SUMMARY")
    print("=" * 70)

    successful = [r for r in results if r.error is None]
    if not successful:
        print("❌ No successful parses to aggregate")
        return

    df = pd.DataFrame([asdict(r) for r in successful])

    print(f"\nTotal resumes: {len(results)}")
    print(f"Successful parses: {len(successful)}")
    print(f"Parse failures: {len(results) - len(successful)}\n")

    print("OVERALL SCORE:")
    print(f"  Mean:  {df['overall_score'].mean():.1f}")

    print("\nSECTION SCORES:")
    sections = [
        ("Identity", "identity_score"),
        ("Skills (F1)", "skills_f1_score"),
        ("Work Experience", "work_experience_score"),
        ("Education", "education_score"),
        ("Projects", "projects_score"),
    ]
    for label, col in sections:
        print(f"  {label:<20} {df[col].mean():.1f}")

    print("\nEXPERIENCE MONTHS:")
    print(f"  Within ±2 months:  {df['total_experience_months_match'].mean():.1%}")

    print("\nHALLUCINATION:")
    print(f"  Resumes with issues: {df['hallucination_detected'].mean():.1%}")
    print(f"  Avg issues/resume:   {df['hallucination_count'].mean():.1f}")

    # Per-resume breakdown
    print("\nPER-RESUME BREAKDOWN:")
    print(f"  {'Name':<30} {'Overall':>7} {'Id':>5} {'Skill':>5} {'Work':>5} {'Edu':>5} {'Proj':>5} {'Halluc':>6}")
    print("  " + "-" * 70)
    for r in successful:
        name = r.filename.replace(".expected.json", "")
        print(f"  {name:<30} {r.overall_score:>7.1f} {r.identity_score:>5.0f} "
              f"{r.skills_f1_score:>5.0f} {r.work_experience_score:>5.0f} "
              f"{r.education_score:>5.0f} {r.projects_score:>5.0f} {r.hallucination_count:>6d}")

    # Save results to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(__file__).parent / "eval_results" / f"eval_results_{timestamp}.csv"
    csv_path.parent.mkdir(exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"\n✓ Results saved to: {csv_path}")

    summary_row = {
        "timestamp": timestamp,
        "total_resumes": len(results),
        "successful_parses": len(successful),
        "overall_score": df['overall_score'].mean(),
        "identity_score": df['identity_score'].mean(),
        "skills_f1_score": df['skills_f1_score'].mean(),
        "work_experience_score": df['work_experience_score'].mean(),
        "education_score": df['education_score'].mean(),
        "projects_score": df['projects_score'].mean(),
        "experience_months_match": df['total_experience_months_match'].mean(),
        "hallucination_rate": df['hallucination_detected'].mean(),
        "avg_hallucination_count": df['hallucination_count'].mean(),
    }
    summary_path = Path(__file__).parent / "eval_summary.csv"
    if summary_path.exists():
        df_summary = pd.read_csv(summary_path)
        df_summary = pd.concat([df_summary, pd.DataFrame([summary_row])], ignore_index=True)
    else:
        df_summary = pd.DataFrame([summary_row])
    df_summary.to_csv(summary_path, index=False)
    print(f"✓ Trend summary saved to: {summary_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate resume parsing quality")
    parser.add_argument("--resume", type=str, help="Specific resume number to run (e.g., '2')")
    parser.add_argument("--verbose", action="store_true", help="Print per-resume details")
    args = parser.parse_args()

    evaluator = ResumeEvaluator(verbose=args.verbose)
    results = evaluator.run_eval(resume_num=args.resume)

    if results:
        print_summary(results)
    else:
        print("❌ No results to summarize")
        sys.exit(1)
