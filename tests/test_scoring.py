# -*- coding: utf-8 -*-
"""六维加权打分模块测试（纯本地，不调用 LLM）。

运行：python tests/test_scoring.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval.scoring import (
    _item_match,
    _normalize,
    dim_coverage,
    rank_jds,
    score_jd,
    DEFAULT_WEIGHTS,
)

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


print("== 归一化 ==")
check("去除空白/标点/小写", _normalize(" 大模型、AIGC、RAG 知识 ") == "大模型aigcrag知识")
check("全角转半角", _normalize("ＩＣ设计") == "ic设计")

print("== 条目匹配 ==")
check("完全相等", _item_match("半导体工艺知识", "半导体工艺知识"))
check("整串包含（浓缩摘录）", _item_match("模拟IC设计经验", "5年以上模拟IC设计经验"))
check("短语滑窗（计算机专业）", _item_match("人工智能/计算机/信息管理专业", "计算机相关专业"))
check("短语滑窗（大模型）", _item_match("大模型、AIGC、RAG、智能客服等AI产品形态知识", "大模型API与Prompt工程认知"))
check("英文 token（Agent）", _item_match("大模型应用设计（智能客服、Agent工作流）", "Agent框架（LangChain/LangGraph）认知"))
check("学历等级特判-满足", _item_match("本科/硕士学历", "本科及以上学历"))
check("学历等级特判-不满足（博士）", not _item_match("本科/硕士学历", "博士学历"))
check("学历等级特判-博士满足本科", _item_match("博士学历", "本科及以上学历"))
check("字符重叠率兜底（沟通）", _item_match("沟通协调能力", "沟通能力好"))
check("无关内容不匹配", not _item_match("原型设计（Axure、Figma）", "精通op-amp/LDO等模拟模块设计"))
check("空字符串不匹配", not _item_match("", "半导体工艺知识"))

print("== 维度得分 ==")
check("JD 无要求 → 1.0", dim_coverage([], []) == 1.0)
check("JD 无要求（候选有）→ 1.0", dim_coverage(["主动性"], []) == 1.0)
check("候选为空 → 0.0", dim_coverage([], ["学习能力强"]) == 0.0)
check("部分覆盖 1/2", dim_coverage(["本科/硕士学历"], ["本科及以上学历", "博士学历"]) == 0.5)
check("全覆盖", dim_coverage(["C/C++编程", "Linux"], ["C/C++编程", "Linux开发"]) == 1.0)

print("== 加权总分 ==")
s = score_jd(
    {"qualifications": ["本科/硕士学历", "人工智能/计算机专业"]},
    {"qualifications": ["计算机相关专业", "本科及以上学历"]},
)
check("任职条件全覆盖", s["total_score"] == 1.0, str(s))
s2 = score_jd(
    {"knowledge": ["大模型知识"], "skill": [], "qualifications": [], "motivation": [], "trait": [], "self_concept": []},
    {"knowledge": ["大模型原理", "分布式训练"], "skill": ["Python"], "qualifications": [], "motivation": [], "trait": [], "self_concept": []},
)
# 空维度 JD 视为满足（1.0），其余 4 个空维度贡献 0.2+0.1+0.1+0.1
expect = DEFAULT_WEIGHTS["knowledge"] * 0.5 + 0.2 + 0.1 + 0.1 + 0.1
check("权重加权正确", abs(s2["total_score"] - expect) < 1e-6, f"{s2['total_score']} vs {expect}")

print("== 权重校验 ==")
try:
    score_jd({}, {}, weights={"unknown": 1.0})
    check("非法维度报错", False)
except ValueError:
    check("非法维度报错", True)
try:
    score_jd({}, {}, weights={"knowledge": 1.0})  # 缺 5 维，自动补 0
    check("缺维自动补 0", True)
except ValueError:
    check("缺维自动补 0", False)

print("== 排序 ==")
ROOT = Path(__file__).resolve().parent.parent
resume = json.load(open(ROOT / "results" / "test2_20260803_171335.json", encoding="utf-8-sig"))
candidate = resume["five_dim"]
jds = []
for p in sorted((ROOT / "jds").glob("*.json")):
    d = json.load(open(p, encoding="utf-8-sig"))
    jds.append({"job_title": d["job_title"], "source": p.name, "five_dim": d["five_dim"]})

ranked = rank_jds(candidate, jds)
titles = [r["job_title"] for r in ranked]
check("按总分降序", all(ranked[i]["total_score"] >= ranked[i + 1]["total_score"] for i in range(len(ranked) - 1)), str(titles))
check("AIGC 排名第一（与 LLM 精排结论一致）", titles[0] == "AIGC应用开发工程师", str(titles))
check("模拟IC 排名最后", titles[-1] == "模拟IC设计工程师", str(titles))
check("分数范围 [0,1]", all(0 <= r["total_score"] <= 1 for r in ranked))
check("topk 截断", len(rank_jds(candidate, jds, topk=3)) == 3)
check("维度得分键齐全", set(ranked[0]["dim_scores"]) == set(DEFAULT_WEIGHTS))
check("jd_matched 计数正确", ranked[0]["dim_scores"]["qualifications"]["jd_matched"] == 2, str(ranked[0]["dim_scores"]["qualifications"]))

print(f"\n结果: {PASSED} 通过, {FAILED} 失败")
sys.exit(1 if FAILED else 0)
