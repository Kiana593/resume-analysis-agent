"""Role 排名 + 覆盖率计算 — 纯函数，零外部依赖。

职责：
- 简历原文 vs 全部 Role 的核心技能覆盖率粗排
- 少条目惩罚（核心技能 < K 的 Role 按 n/K 打折）
- 按技能跨 Role 稀有度做 IDF 重加权
- 七维命中明细
"""

import copy
import math
from typing import Any, Dict, List, Optional, Sequence

from .matching import match_skills_in_text

# 少技能惩罚阈值：核心技能数少于该值的 Role，覆盖率按 n/K 打折
CORE_SKILL_PENALTY_K = 10


def _apply_idf(roles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按技能的跨 Role 稀有度重新加权（IDF 思想）。

    weight' = weight × log(N / df)
    - N: 有核心技能的 Role 总数
    - df: 该技能出现在多少个 Role 中（document frequency）
    通用技能 df 大 → 权重趋近 0；职业特有技能 df 小 → 权重保留。

    Args:
        roles: 原始 Role 列表（不改动原数据）。

    Returns:
        深拷贝并重新加权后的 Role 列表。
    """
    weighted = copy.deepcopy(list(roles))

    # 统计每个技能出现的 Role 数（df）
    df: Dict[str, int] = {}
    for role in weighted:
        for skill in role.get("skills", []):
            name = skill.get("name", "")
            if name:
                df[name] = df.get(name, 0) + 1

    n_roles = sum(1 for r in weighted if r.get("skills"))
    if n_roles <= 1:
        return weighted

    for role in weighted:
        for skill in role.get("skills", []):
            name = skill.get("name", "")
            if not name:
                continue
            doc_freq = max(1, df.get(name, 1))
            idf = math.log(n_roles / doc_freq)
            skill["weight"] = skill["weight"] * max(idf, 0.0)

    return weighted


def rank_roles(
    raw_text: str,
    roles: Sequence[Dict[str, Any]],
    topk: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """简历原文 vs 全部 Role 的核心技能覆盖率粗排。

    直接在简历原文（Markdown）中做归一化子串搜索，
    不依赖任何提取结果。

    Args:
        raw_text: 简历 Markdown 原文。
        roles: store 层加载的 Role 列表（role_name/skills/jd_count 等）。
        topk: 只返回前 N 名（None/<=0 返回全部）。

    Returns:
        按 score 降序的列表，每条：
        {"role_name", "family_name", "domain_name", "jd_count",
         "score", "hit_skills", "total_skills"}
    """
    roles = _apply_idf(roles)

    scored: List[Dict[str, Any]] = []
    for role in roles:
        if role.get("jd_count", 0) <= 0:
            continue
        skills = [s for s in role.get("skills", []) if s.get("name")]
        if not skills:
            continue

        result = match_skills_in_text(raw_text, skills)

        n_skills = len(skills)
        penalty = min(1.0, n_skills / CORE_SKILL_PENALTY_K)
        score = round((result["hit_count"] / max(n_skills, 1)) * penalty, 4)

        scored.append(
            {
                "role_name": role.get("role_name", ""),
                "family_name": role.get("family_name", ""),
                "domain_name": role.get("domain_name", ""),
                "jd_count": role.get("jd_count", 0),
                "score": score,
                "hit_skills": result["hit_count"],
                "total_skills": n_skills,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    if topk and topk > 0:
        scored = scored[:topk]
    return scored


def compute_dimension_hits(
    raw_text: str,
    role_skills: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """按七维统计简历原文对 Role 技能的命中/未命中明细。

    Args:
        raw_text: 简历 Markdown 原文。
        role_skills: 单个 Role 的技能列表 [{name, category, weight, rank}, ...]。

    Returns:
        {"knowledge": {"hit": [...], "miss": [...], "coverage": 0.67,
                       "total": 3, "hit_count": 2, "miss_count": 1}, ...}
    """
    full = match_skills_in_text(raw_text, role_skills)
    result: Dict[str, Dict[str, Any]] = {}
    for dim, bd in full.get("by_dim", {}).items():
        names_hit = [e["name"] for e in bd.get("hit", [])]
        names_miss = [e["name"] for e in bd.get("miss", [])]
        total = bd["total"]
        result[dim] = {
            "hit": names_hit,
            "miss": names_miss,
            "coverage": round(bd["hit_count"] / max(total, 1), 4),
            "total": total,
            "hit_count": bd["hit_count"],
            "miss_count": len(names_miss),
        }
    return result
