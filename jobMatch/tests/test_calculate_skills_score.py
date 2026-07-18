import pytest
from calculate_skills_score import SkillsMatcher

matcher = SkillsMatcher()


class TestNormalizeSkill:

    def test_canonical_skill_returns_itself(self):
        assert matcher.normalize_skill("javascript") == "javascript"

    def test_alias_maps_to_canonical(self):
        assert matcher.normalize_skill("js") == "javascript"
        assert matcher.normalize_skill("ts") == "typescript"
        assert matcher.normalize_skill("golang") == "go"

    def test_case_insensitivity(self):
        assert matcher.normalize_skill("JavaScript") == "javascript"
        assert matcher.normalize_skill("REACT") == "react"
        assert matcher.normalize_skill("C++") == "cpp"

    def test_js_suffix_stripped(self):
        assert matcher.normalize_skill("react.js") == "react"
        assert matcher.normalize_skill("node.js") == "node"

    def test_py_suffix_stripped(self):
        assert matcher.normalize_skill("tensorflow.py") == "tensorflow"

    def test_parenthetical_content_removed(self):
        assert matcher.normalize_skill("javascript (es6+)") == "javascript"
        assert matcher.normalize_skill("React (library)") == "react"

    def test_unix_suffix_stripped(self):
        assert matcher.normalize_skill("linux/unix") == "linux"

    def test_unknown_skill_passes_through_lowercased(self):
        assert matcher.normalize_skill("Zig") == "zig"
        assert matcher.normalize_skill("Kotlin") == "kotlin"

    def test_empty_string(self):
        assert matcher.normalize_skill("") == ""

    def test_whitespace_trimmed(self):
        assert matcher.normalize_skill("  python  ") == "python"

    def test_multi_word_skill(self):
        assert matcher.normalize_skill("machine learning") == "machinelearning"

    def test_agile_variants(self):
        assert matcher.normalize_skill("agile methodology") == "agile"


class TestCalculateSkillsScore:

    def test_all_required_matched(self):
        result = matcher.calculate_skills_score(
            ["python", "javascript"], ["python", "javascript"]
        )
        assert result["skills_score"] == 1.0
        assert result["total_matched_required"] == 2
        assert result["total_required"] == 2

    def test_none_matched(self):
        result = matcher.calculate_skills_score(
            ["python"], ["java", "c++"]
        )
        assert result["skills_score"] == 0.0
        assert result["total_matched_required"] == 0
        assert result["required_match_ratio"] == 0.0

    def test_partial_match(self):
        result = matcher.calculate_skills_score(
            ["python", "docker"], ["python", "java", "c++"]
        )
        assert result["skills_score"] == pytest.approx(1 / 3, rel=1e-2)
        assert result["total_matched_required"] == 1
        assert result["total_required"] == 3

    def test_nice_to_have_bonus_lifts_score(self):
        result = matcher.calculate_skills_score(
            user_skills=["python", "docker", "aws"],
            job_required=["python"],
            job_nice_to_have=["docker", "aws"],
        )
        assert result["skills_score"] == 1.0
        assert sorted(result["matched_nice_to_have"]) == sorted(["docker", "aws"])

    def test_nice_to_have_bonus_capped_at_20_percent(self):
        many_skills = [f"s{i}" for i in range(20)]
        result = matcher.calculate_skills_score(
            user_skills=["python"] + many_skills,
            job_required=["python"],
            job_nice_to_have=many_skills,
        )
        assert result["skills_score"] == 1.0

    def test_nice_to_have_without_all_matched(self):
        result = matcher.calculate_skills_score(
            user_skills=["python"],
            job_required=["python"],
            job_nice_to_have=["docker", "aws"],
        )
        assert result["skills_score"] == 1.0
        assert result["matched_nice_to_have"] == []
        assert len(result["unmatched_nice_to_have"]) == 2

    def test_empty_required_skills_defaults_to_perfect_ratio(self):
        result = matcher.calculate_skills_score(
            user_skills=["python"], job_required=[]
        )
        assert result["skills_score"] == 1.0
        assert result["required_match_ratio"] == 1.0
        assert result["total_required"] == 0

    def test_empty_user_skills_scores_zero(self):
        result = matcher.calculate_skills_score(
            user_skills=[], job_required=["python", "java"]
        )
        assert result["skills_score"] == 0.0
        assert result["total_matched_required"] == 0

    def test_none_nice_to_have_treated_as_empty(self):
        result = matcher.calculate_skills_score(
            user_skills=["python"], job_required=["python"], job_nice_to_have=None
        )
        assert result["skills_score"] == 1.0
        assert result["matched_nice_to_have"] == []

    def test_normalization_bridges_variant_skill_names(self):
        result = matcher.calculate_skills_score(
            user_skills=["JS", "React.js", "ML"],
            job_required=["javascript", "react", "machinelearning"],
        )
        assert result["skills_score"] == 1.0
        assert result["total_matched_required"] == 3

    def test_return_dict_has_all_expected_keys(self):
        result = matcher.calculate_skills_score(["python"], ["python"])
        expected_keys = {
            "skills_score",
            "required_match_ratio",
            "total_required",
            "total_matched_required",
            "matched_required",
            "unmatched_required",
            "matched_nice_to_have",
            "unmatched_nice_to_have",
        }
        assert set(result.keys()) == expected_keys

    def test_duplicate_skills_deduped_by_normalization(self):
        result = matcher.calculate_skills_score(
            user_skills=["python", "Python", "PYTHON"],
            job_required=["python", "java"],
        )
        assert result["total_matched_required"] == 1
