"""排名与覆盖率计算测试 — 纯函数，零外部依赖。"""
import pytest

from src.core.ranking import _apply_idf, compute_dimension_hits, rank_roles


def _role(name, jd_count, skills):
    return {
        "role_name": name,
        "family_name": "算法",
        "domain_name": "AI",
        "jd_count": jd_count,
        "skills": [
            {"name": s, "category": "技术", "weight": 1.0, "rank": i + 1}
            for i, s in enumerate(skills)
        ],
    }


class TestApplyIdf:
    def test_single_role_unchanged(self):
        roles = [_role("R1", 1, ["Python", "PyTorch"])]
        out = _apply_idf(roles)
        assert all(s["weight"] == 1.0 for s in out[0]["skills"])

    def test_shared_skill_penalized(self):
        roles = [
            _role("R1", 1, ["通用技能", "特有A"]),
            _role("R2", 1, ["通用技能", "特有B"]),
        ]
        out = _apply_idf(roles)
        weights = {s["name"]: s["weight"] for s in out[0]["skills"]}
        assert weights["通用技能"] < 1.0
        assert weights["特有A"] > weights["通用技能"]

    def test_input_not_mutated(self):
        roles = [
            _role("R1", 1, ["通用技能", "特有A"]),
            _role("R2", 1, ["通用技能", "特有B"]),
        ]
        _apply_idf(roles)
        assert all(s["weight"] == 1.0 for r in roles for s in r["skills"])


class TestRankRoles:
    def test_descending_and_range(self):
        roles = [
            _role("全命中", 2, ["Python", "PyTorch", "深度学习", "机器学习"]),
            _role("半命中", 2, ["Python", "C++", "Java", "Go"]),
        ]
        ranked = rank_roles("Python、PyTorch、深度学习、机器学习", roles)
        assert ranked[0]["role_name"] == "全命中"
        assert ranked[0]["score"] >= ranked[1]["score"]
        assert all(0 <= r["score"] <= 1 for r in ranked)

    def test_topk_truncation(self):
        roles = [
            _role("R1", 1, ["Python", "PyTorch", "C++"]),
            _role("R2", 1, ["Java", "Go", "Rust"]),
            _role("R3", 1, ["React", "Vue", "Node"]),
        ]
        assert len(rank_roles("Python PyTorch C++", roles, topk=2)) == 2

    def test_few_skills_penalty(self):
        # 1 个技能全命中 → 覆盖率 1.0，但少条目惩罚 1/10 → 0.1
        roles = [_role("少条目", 1, ["Python"])]
        ranked = rank_roles("熟悉 Python", roles)
        assert ranked[0]["score"] == pytest.approx(0.1)

    def test_skips_zero_jd(self):
        roles = [_role("无JD", 0, ["Python"]), _role("正常", 1, ["Python"])]
        ranked = rank_roles("Python", roles)
        assert [r["role_name"] for r in ranked] == ["正常"]

    def test_skips_no_skills(self):
        roles = [_role("无技能", 1, [])]
        assert rank_roles("Python", roles) == []


class TestComputeDimensionHits:
    def test_structure_and_coverage(self):
        skills = [
            {"name": "深度学习", "category": "知识", "weight": 1.0, "rank": 1},
            {"name": "PyTorch", "category": "技术", "weight": 1.0, "rank": 2},
            {"name": "C++", "category": "技术", "weight": 1.0, "rank": 3},
        ]
        result = compute_dimension_hits("深度学习与PyTorch", skills)
        assert result["knowledge"]["hit_count"] == 1
        assert result["knowledge"]["coverage"] == 1.0
        assert result["skill"]["hit_count"] == 1
        assert result["skill"]["miss"] == ["C++"]
        assert result["skill"]["coverage"] == 0.5
        assert result["skill"]["total"] == 2

    def test_empty_skills(self):
        assert compute_dimension_hits("Python", []) == {}
