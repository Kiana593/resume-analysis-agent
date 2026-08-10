"""工具层测试 — 注入内存 store / 假 LLM，不触网。"""
import os

import pytest

from src.store import MemoryRoleStore
from src.tools.analyze import analyze_gap
from src.tools.enhance import enhance_matches
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


class TestRenderRadar:
    def test_png_generated(self, tmp_path, memory_store):
        rank_result = rank_resume("Python", topk=1, store=memory_store)
        out = tmp_path / "radar.png"
        path = render_radar(rank_result["results"][0], output_path=str(out))
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
