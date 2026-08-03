# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
sys.path.insert(0, r"D:\个人资料\26暑假科研项目\小挑\简历提取分析agent")
from src.nodes.verify_output import verify_output, _evidence_matches, _flatten_five_dim
from src.nodes.data_loader import load_jd
from src.nodes.gap_analysis import _format_five_dim
from src.nodes.learning_path import _flatten_dimension_inputs

jd = json.load(open(r"D:\个人资料\26暑假科研项目\小挑\简历提取分析agent\jds\01_ic_design.json", encoding="utf-8-sig"))
jd_raw = jd["raw_text"]
jd_extra = _flatten_five_dim(jd["five_dim"])
resume_raw = "XX大学 人工智能专业 本科。负责智能客服系统从0到1设计，团队协作良好。学习能力强。"

def dim(d, level, summary, missing=None, satisfied=None):
    return {"gap_level": level, "summary": summary,
            "missing": missing or [], "satisfied": satisfied or []}

def base():
    return {
        "resume_data": {"raw_text": resume_raw, "five_dim": {"knowledge": ["AI产品设计"], "trait": ["学习能力强"], "self_concept": ["团队合作意识"]}},
        "jd_data": {"raw_text": jd_raw, "five_dim": jd["five_dim"]},
    }

# 1. 合法输出（核心维度 missing → no）通过
s = base()
s["analysis_result"] = {
    "dimensions": {
        "knowledge": dim("knowledge", "missing", "专业知识完全不符，无IC设计背景", ["半导体工艺知识", "IC设计流程"], []),
        "skill": dim("skill", "missing", "核心技能缺失，无模拟电路/EDA经验", ["模拟模块设计", "EDA工具"], []),
        "qualifications": dim("qualifications", "missing", "专业不符，本科人工智能方向", ["电子信息相关专业"], []),
        "motivation": dim("motivation", "sufficient", "主动性在项目中有体现", [], ["主观能动性强"]),
        "trait": dim("trait", "partial", "学习能力符合，工程细致度待验证", ["认真仔细"], ["学习能力强"]),
        "self_concept": dim("self_concept", "sufficient", "团队协作在项目中有体现", [], ["团队合作意识"]),
    },
    "extra_gaps": [],
    "learning_path": [{"step": 1, "skill": "电路原理", "importance": "high"}],
    "match": {"verdict": "no", "reason": "专业与核心技能均不满足，不匹配"},
    "overall_summary": "整体不匹配",
}
r1 = verify_output(s)
print("1) 合法输出通过:", "PASS" if r1["verify_result"]["passed"] else "FAIL -> " + str(r1["verify_result"]["errors"]))

# 2. 缺维度 → 拦截
s2 = base()
s2["analysis_result"] = {
    "dimensions": {"knowledge": dim("knowledge", "missing", "专业知识不符", ["IC设计流程"], [])},
    "extra_gaps": [], "learning_path": [], "match": {"verdict": "no", "reason": "r"}, "overall_summary": "s",
}
r2 = verify_output(s2)
print("2) 缺维度拦截:", "PASS" if not r2["verify_result"]["passed"] else "FAIL")

# 3. verdict 不一致拦截
s3 = base()
s3["analysis_result"] = {
    "dimensions": {
        "knowledge": dim("knowledge", "missing", "专业知识不符", ["IC设计流程"], []),
        "skill": dim("skill", "sufficient", "满足", [], ["EDA工具"]),
        "qualifications": dim("qualifications", "sufficient", "满足", [], []),
        "motivation": dim("motivation", "sufficient", "满足", [], []),
        "trait": dim("trait", "sufficient", "满足", [], []),
        "self_concept": dim("self_concept", "sufficient", "满足", [], []),
    },
    "extra_gaps": [], "learning_path": [{"step": 1, "skill": "x", "importance": "high"}],
    "match": {"verdict": "yes", "reason": "r"},
}
r3 = verify_output(s3)
print("3) verdict不一致拦截:", "PASS" if not r3["verify_result"]["passed"] else "FAIL")

# 4. summary 伪造拦截（summary 完全不在原文）
s4 = base()
s4["analysis_result"] = {
    "dimensions": {
        "knowledge": dim("knowledge", "missing", "候选人掌握量子芯片核心技术", [], []),
        "skill": dim("skill", "sufficient", "满足", [], []),
        "qualifications": dim("qualifications", "sufficient", "满足", [], []),
        "motivation": dim("motivation", "sufficient", "满足", [], []),
        "trait": dim("trait", "sufficient", "满足", [], []),
        "self_concept": dim("self_concept", "sufficient", "满足", [], []),
    },
    "extra_gaps": [], "learning_path": [{"step": 1, "skill": "x", "importance": "high"}],
    "match": {"verdict": "no", "reason": "r"},
}
r4 = verify_output(s4)
print("4) summary伪造拦截:", "PASS" if not r4["verify_result"]["passed"] else "FAIL")

# 5. learning_path 扁平化
flat = _flatten_dimension_inputs(s["analysis_result"]["dimensions"], [{"item": "加分项", "importance": "low"}])
print("5) 维度扁平化:", "PASS" if len(flat) == 7 and flat[0]["importance"] == "high" else f"FAIL -> {flat}")

# 6. 浓缩词匹配 + 伪造拦截
print("6) 浓缩词通过:", "PASS" if _evidence_matches("熟练EDA工具", jd_raw, jd_extra) else "FAIL")
print("   伪造拦截:", "PASS" if not _evidence_matches("我有十年大模型训练经验", jd_raw, jd_extra) else "FAIL")

# 7. 六维格式化
f = _format_five_dim(jd["five_dim"])
print("7) 六维格式化:", "PASS" if "任职条件" in f else "FAIL")
