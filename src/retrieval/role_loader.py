"""Role（标准职业）数据接入 + 粗排算法。

阶段 1 粗排：简历 vs 134 个标准职业（Role）。
每个 Role 通过 HAS_CORE_SKILL 关系挂载核心技能（带 final_score 权重、rank 排名），
粗排公式 = Σ(简历命中的核心技能 × final_score) / Σ(全部核心技能 final_score)。

图谱结构（resume-handoff.dump）：
    Role -[:HAS_CORE_SKILL {final_score, rank}]-> NormalizedSkill
    JD -[:INSTANCE_OF]-> Role
"""

import copy
import math
import os
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

from src.retrieval.scoring import _item_match, CATEGORY_TO_DIM, match_skills_in_text

load_dotenv()


# ==================== Role 加载 ====================

def load_roles_from_neo4j() -> List[Dict[str, Any]]:
    """从 Neo4j 加载全部 Role 及其核心技能。

    Returns:
        每个元素：
        {
            "role_name": str,
            "family_name": str,
            "domain_name": str,
            "jd_count": int,
            "skills": [
                {"name": str, "category": str, "weight": float, "rank": int},
                ...
            ],
        }
    """
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not password:
        raise ValueError("未配置 NEO4J_PASSWORD 环境变量。请在 .env 中设置。")

    from neo4j import GraphDatabase

    cypher = """
        MATCH (role:Role)-[edge:HAS_CORE_SKILL]->(skill:NormalizedSkill)
        OPTIONAL MATCH (jd:JD)-[:INSTANCE_OF]->(role)
        WITH role, edge, skill, count(DISTINCT jd) AS jd_cnt
        ORDER BY role.role_name, edge.rank
        RETURN role.role_name AS role_name,
               role.family_name AS family_name,
               role.domain_name AS domain_name,
               jd_cnt,
               collect({
                   name: skill.canonical_name,
                   category: skill.category,
                   weight: coalesce(edge.final_score, 0.0),
                   rank: coalesce(edge.rank, 9999)
               }) AS skills
    """

    driver = GraphDatabase.driver(uri, auth=(user, password))
    roles: List[Dict[str, Any]] = []
    try:
        with driver.session(database=database) as session:
            result = session.run(cypher)
            for record in result:
                roles.append(_build_role_entry(record))
    finally:
        driver.close()

    if not roles:
        raise RuntimeError("Neo4j 中未找到 Role-HAS_CORE_SKILL 数据。请检查图谱是否完整。")

    return roles


def _build_role_entry(record) -> Dict[str, Any]:
    """将 Neo4j Role 记录转为标准结构。"""
    skills = []
    for item in record.get("skills", []) or []:
        skills.append(
            {
                "name": str(item.get("name", "")).strip(),
                "category": str(item.get("category", "")).strip(),
                "weight": float(item.get("weight", 0.0) or 0.0),
                "rank": int(item.get("rank", 9999) or 9999),
            }
        )
    return {
        "role_name": record.get("role_name", "Unknown"),
        "family_name": record.get("family_name", ""),
        "domain_name": record.get("domain_name", ""),
        "jd_count": record.get("jd_cnt", 0),
        "skills": skills,
    }


# ==================== Role 粗排算法 ====================

# 少技能惩罚阈值：核心技能数少于该值的 Role，覆盖率按 n/K 打折
CORE_SKILL_PENALTY_K = 10


def rank_roles(
    raw_text: str,
    roles: Sequence[Dict[str, Any]],
    topk: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """简历原文 vs 全部 Role 的核心技能覆盖率粗排。

    直接在简历原文（Markdown）中做归一化子串搜索，
    不再依赖六维提取结果。

    Args:
        raw_text: 简历 Markdown 原文。
        roles: load_roles_from_neo4j() 的返回。
        topk: 只返回前 N 名（None/<=0 返回全部）。
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


# ==================== dimension hit details ====================

DIM_LABELS = {
    "knowledge": "知识",
    "skill": "技术",
    "qualifications": "任职条件",
    "preference": "招聘偏好",
    "motivation": "动机",
    "trait": "特质",
    "self_concept": "自我概念",
}


def compute_dimension_hits(candidate_five_dim, role_skills):
    dim_skills = {}
    for sk in role_skills:
        name = sk.get("name", "").strip()
        if not name:
            continue
        dim = CATEGORY_TO_DIM.get(sk.get("category", ""))
        if dim:
            dim_skills.setdefault(dim, []).append(sk)

    result = {}
    for dim in ("knowledge", "skill", "qualifications", "motivation", "trait", "self_concept"):
        skills = dim_skills.get(dim, [])
        if not skills:
            continue
        candidate_items = candidate_five_dim.get(dim, [])
        hit = []
        miss = []
        for sk in sorted(skills, key=lambda s: s.get("rank", 9999)):
            name = sk["name"]
            matched = any(_item_match(c, name) for c in candidate_items)
            (hit if matched else miss).append(name)
        result[dim] = {
            "hit": hit,
            "miss": miss,
            "coverage": round(len(hit) / len(skills), 4) if skills else 1.0,
            "total": len(skills),
            "hit_count": len(hit),
            "miss_count": len(miss),
        }
    return result


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


# ==================== 候选 Role 旗下 JD ====================

def load_jds_by_roles(
    role_names: Sequence[str],
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """加载指定 Role 旗下的全部 JD（含直接技能）。

    Args:
        role_names: Role 名称列表（来自阶段 1 粗排）。
        limit: 可选，每个 Role 限制返回 JD 数。

    Returns:
        符合数据契约的 JD 列表（five_dim 来自 MENTIONS_NORMALIZED_SKILL，
        通常需配合 csv_loader.enrich_jds_with_csv 增强为完整六维）。
    """
    if not role_names:
        return []

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not password:
        raise ValueError("未配置 NEO4J_PASSWORD 环境变量。")

    from neo4j import GraphDatabase

    limit_clause = f"LIMIT {limit}" if limit else ""
    cypher = f"""
        MATCH (jd:JD)-[:INSTANCE_OF]->(role:Role)
        WHERE role.role_name IN $role_names
        MATCH (jd)-[:MENTIONS_NORMALIZED_SKILL]->(sk:NormalizedSkill)
        WITH jd, role, sk
        ORDER BY sk.verified_rate DESC
        WITH jd, role,
             collect(DISTINCT {{name: sk.canonical_name, cat: sk.category}}) AS skills
        RETURN jd.title AS job_title,
               jd.description AS raw_text,
               jd.company_name AS company_name,
               jd.salary AS salary,
               jd.experience AS experience,
               jd.source_file AS source_file,
               elementId(jd) AS node_id,
               role.role_name AS role_name,
               skills
        {limit_clause}
    """

    driver = GraphDatabase.driver(uri, auth=(user, password))
    jds: List[Dict[str, Any]] = []
    try:
        with driver.session(database=database) as session:
            result = session.run(cypher, {"role_names": list(role_names)})
            for record in result:
                entry = {
                    "job_title": record.get("job_title", "Unknown"),
                    "source": f"neo4j://{record.get('node_id', '')}",
                    "source_file": record.get("source_file", ""),
                    "company_name": record.get("company_name", ""),
                    "salary": record.get("salary", ""),
                    "experience": record.get("experience", ""),
                    "raw_text": record.get("raw_text", ""),
                    "role_name": record.get("role_name", ""),
                }
                five_dim = {dim: [] for dim in (
                    "knowledge", "skill", "qualifications",
                    "motivation", "trait", "self_concept")}
                for item in record.get("skills", []) or []:
                    dim = CATEGORY_TO_DIM.get(item.get("cat", ""))
                    name = str(item.get("name", "")).strip()
                    if dim and name and name not in five_dim[dim]:
                        five_dim[dim].append(name)
                entry["five_dim"] = five_dim
                jds.append(entry)
    finally:
        driver.close()

    return jds

