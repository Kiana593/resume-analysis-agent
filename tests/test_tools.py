"""工具层测试 — 注入内存 store / 假 LLM，不触网。"""
import os

import pytest

from src.store import MemoryRoleStore
from src.tools.analyze import analyze_gap, prepare_gap
from src.tools.enhance import apply_enhance_review, enhance_matches, prepare_enhance
from src.tools.modify import (
    find_ai_phrases,
    format_modify_markdown,
    prepare_resume_edit,
    suggest_resume_edit,
    validate_resume_edit,
)
from src.tools.rank import rank_resume
from src.tools.visualize import render_radar


@pytest.fixture
def memory_store():
    return MemoryRoleStore()


def _fake_llm(result):
    def caller(prompt):
        assert prompt  # 提示词非空
        return result

    return caller


class TestRankResume:
    def test_returns_top_results_sorted(self, memory_store):
        result = rank_resume("熟悉 Python 与 PyTorch，做深度学习", topk=5, store=memory_store)
        assert result["count"] > 0
        assert 0 < len(result["results"]) <= 5
        scores = [r["score"] for r in result["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_result_shape(self, memory_store):
        result = rank_resume("Python", topk=1, store=memory_store)
        item = result["results"][0]
        for key in ("role_name", "family_name", "domain_name", "score",
                    "hit_skills", "total_skills", "dimensions"):
            assert key in item

    def test_empty_text_raises(self, memory_store):
        with pytest.raises(ValueError):
            rank_resume("   ", store=memory_store)


class TestEnhanceMatches:
    def test_with_injected_llm(self, memory_store):
        rank_result = rank_resume("Python", topk=3, store=memory_store)
        fake = _fake_llm({
            "topk": len(rank_result["results"]),
            "results": [
                {
                    "role_name": r["role_name"],
                    "score": r["score"],
                    "hit_skills": r["hit_skills"],
                    "total_skills": r["total_skills"],
                    "review_note": "无修正",
                    "dimensions": {},
                }
                for r in rank_result["results"]
            ],
        })
        out = enhance_matches(rank_result, "Python", topk=3, llm_func=fake)
        assert out["topk"] == len(rank_result["results"])

    def test_empty_rank_result_raises(self, memory_store):
        with pytest.raises(ValueError):
            enhance_matches({}, "Python", llm_func=_fake_llm({}))


class TestPrepareEnhance:
    def test_payload_shape(self, memory_store):
        rank_result = rank_resume("Python", topk=2, store=memory_store)
        payload = prepare_enhance(rank_result, "Python", topk=2)
        assert payload["mode"] == "agent_review"
        assert payload["rank_data"]["topk"] == 2
        assert "prompt" in payload and "resume_text" in payload


class TestApplyEnhanceReview:
    def test_merge_moves_misses_to_hits(self, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        raw = rank_result["results"][0]
        review = {
            "topk": 1,
            "results": [
                {
                    "role_name": raw["role_name"],
                    "review_note": "全部命中",
                    "dimensions": {
                        dim: {"hit": detail["hit"] + detail["miss"], "miss": []}
                        for dim, detail in raw["dimensions"].items()
                    },
                }
            ],
        }
        out = apply_enhance_review(rank_result, review)
        item = out["results"][0]
        assert item["role_name"] == raw["role_name"]
        assert item["hit_skills"] == item["total_skills"]
        assert item["review_note"] == "全部命中"
        assert item["score"] > raw["score"]

    def test_invalid_review_raises(self, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        with pytest.raises(ValueError):
            apply_enhance_review(rank_result, {})


class TestAnalyzeGap:
    def test_with_injected_llm(self, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        role = rank_result["results"][0]
        fake = _fake_llm({
            "match": {"verdict": "yes", "reason": "核心技能匹配"},
            "dimensions": {"skill": {"summary": "技术维度匹配"}},
            "overall_summary": "整体匹配，建议补充项目经历",
            "learning_path": [{"step": 1, "skill": "PyTorch", "importance": "高"}],
        })
        out = analyze_gap(role, "Python", llm_func=fake)
        assert out["role_name"] == role["role_name"]
        assert "### 学习路径" in out["markdown"]
        assert "整体匹配" in out["markdown"]

    def test_empty_resume_raises(self, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        with pytest.raises(ValueError):
            analyze_gap(rank_result["results"][0], "", llm_func=_fake_llm({}))

    def test_invalid_role_raises(self, memory_store):
        with pytest.raises(ValueError):
            analyze_gap({}, "Python", llm_func=_fake_llm({}))


class TestPrepareGap:
    def test_payload_shape(self, memory_store):
        role = rank_resume("Python", topk=1, store=memory_store)["results"][0]
        payload = prepare_gap(role, "Python")
        assert payload["mode"] == "agent_analysis"
        assert payload["role_name"] == role["role_name"]
        assert "prompt" in payload and "dimension_details" in payload


class TestModify:
    def test_prepare_payload(self, memory_store):
        role = rank_resume("Python", topk=1, store=memory_store)["results"][0]
        payload = prepare_resume_edit(role, "熟悉 Python")
        assert payload["mode"] == "agent_edit"
        assert payload["role_name"] == role["role_name"]
        assert "prompt" in payload and "ai_phrase_blacklist" in payload

    def test_suggest_with_fake_llm(self, memory_store):
        role = rank_resume("Python", topk=1, store=memory_store)["results"][0]
        fake = _fake_llm(
            {
                "target_role": role["role_name"],
                "summary": "整体匹配，建议强化项目表述",
                "suggestions": [
                    {
                        "skill": "Python",
                        "status": "reinforce",
                        "suggestion": "在项目描述中突出 Python 使用场景",
                        "example_rewrite": "用 Python 实现数据清洗流程",
                    }
                ],
                "risks": ["不要虚构量化指标"],
            }
        )
        out = suggest_resume_edit(role, "熟悉 Python", llm_func=fake)
        assert out["role_name"] == role["role_name"]
        assert "### 1. Python" in out["markdown"]
        assert "风险提示" in out["markdown"]

    def test_format_markdown_missing_status(self):
        md = format_modify_markdown(
            "测试岗位",
            {
                "summary": "结论",
                "suggestions": [
                    {"skill": "C++", "status": "missing", "suggestion": "不建议写入"}
                ],
            },
        )
        assert "（缺失）" in md

    def test_find_ai_phrases(self):
        assert "赋能" in find_ai_phrases("通过平台赋能业务")
        assert find_ai_phrases("正常表述") == []


class TestValidateResumeEdit:
    def _role(self, memory_store):
        return rank_resume("Python", topk=1, store=memory_store)["results"][0]

    def test_valid_suggestions_pass(self, memory_store):
        role = self._role(memory_store)
        edit = {
            "target_role": role["role_name"],
            "suggestions": [
                {"skill": "Python", "status": "hit", "suggestion": "保持现有描述"}
            ],
        }
        out = validate_resume_edit(role, "熟悉 Python", edit)
        assert out["valid"] is True
        assert out["stats"]["suggestions"] == 1

    def test_fabricated_metric_warns(self, memory_store):
        role = self._role(memory_store)
        edit = {
            "suggestions": [
                {
                    "skill": "Python",
                    "status": "hit",
                    "suggestion": "吞吐量提升50%",
                }
            ],
        }
        out = validate_resume_edit(role, "熟悉 Python", edit)
        assert any(v["field"] == "suggestion" and "50%" in v["message"] for v in out["violations"])
        assert out["stats"]["fabricated_metrics"] >= 1

    def test_missing_skill_without_grounding_is_critical(self, memory_store):
        role = self._role(memory_store)
        miss_skill = next(
            s for detail in role["dimensions"].values() for s in detail.get("miss", [])
        )
        edit = {
            "suggestions": [
                {
                    "skill": miss_skill,
                    "status": "missing",
                    "suggestion": "建议补充",
                    "example_rewrite": f"掌握{miss_skill}进行开发",
                }
            ],
        }
        out = validate_resume_edit(role, "熟悉 Python", edit)
        assert out["valid"] is False
        assert any(v["level"] == "critical" for v in out["violations"])

    def test_unknown_skill_warns(self, memory_store):
        role = self._role(memory_store)
        edit = {
            "suggestions": [
                {"skill": "不存在的技能XYZ", "status": "hit", "suggestion": "保持"}
            ],
        }
        out = validate_resume_edit(role, "熟悉 Python", edit)
        assert any(v["field"] == "skill" and "不在岗位核心技能清单" in v["message"] for v in out["violations"])
        assert out["stats"]["unknown_skills"] == 1

    def test_invalid_structure(self, memory_store):
        role = self._role(memory_store)
        out = validate_resume_edit(role, "熟悉 Python", {"suggestions": "oops"})
        assert out["valid"] is False
        assert out["checklist"]["structure"] is False

    def test_negative_directive_not_critical(self, memory_store):
        role = self._role(memory_store)
        miss_skill = next(
            s for detail in role["dimensions"].values() for s in detail.get("miss", [])
        )
        edit = {
            "suggestions": [
                {
                    "skill": miss_skill,
                    "status": "missing",
                    "suggestion": f"简历未涉及{miss_skill}，不建议硬写",
                }
            ],
        }
        out = validate_resume_edit(role, "熟悉 Python", edit)
        assert out["valid"] is True
        assert not any(v["level"] == "critical" for v in out["violations"])


class TestRenderRadar:
    def test_png_generated(self, tmp_path, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        out = tmp_path / "radar.png"
        path = render_radar(rank_result["results"][0], output_path=str(out))
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
