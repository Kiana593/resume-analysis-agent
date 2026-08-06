"""Neo4j 图图谱匹配模块 — 对接 resume-graph-match 知识图谱。

提供基于图结构的简历-JD匹配算法，作为六维文本打分（scoring.py）的补充。
当 resume-graph-match 仓库克隆到本地后，可通过设置环境变量启用：
    RESUME_GRAPH_MATCH_PATH=/path/to/resume-graph-match

功能：
    1. 图嵌入相似度匹配（Graph Embedding Similarity）
    2. 基于路径的关联匹配（Path-based Relevance）
    3. 社区检测与聚类推荐（Community Detection）
    4. 混合排序（Hybrid Ranking）：图匹配得分 + 六维文本得分

图谱结构（resume-handoff.dump）：
    节点: JD / Role / NormalizedSkill
    关系: JD-[:MENTIONS_NORMALIZED_SKILL]->NormalizedSkill
          JD-[:INSTANCE_OF]->Role
          Role-[:HAS_CORE_SKILL]->NormalizedSkill
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import importlib.util


# ==================== resume-graph-match 动态加载 ====================

def _find_graph_match_module():
    """动态查找并加载 resume-graph-match 模块。"""
    # 1. 环境变量优先
    env_path = os.getenv("RESUME_GRAPH_MATCH_PATH", "")
    if env_path:
        pkg_path = Path(env_path)
    else:
        # 2. 查找项目同级的 resume-graph-match 目录
        candidate = Path(__file__).resolve().parent.parent.parent.parent / "resume-graph-match"
        if candidate.is_dir():
            pkg_path = candidate
        else:
            return None

    if not pkg_path.is_dir():
        return None

    src_path = pkg_path / "src"
    if src_path.is_dir():
        sys.path.insert(0, str(src_path))
    else:
        sys.path.insert(0, str(pkg_path))

    try:
        mod = importlib.import_module("graph_match")
        return mod
    except ImportError:
        # 尝试其他可能的模块名
        for name in ("matcher", "matching", "resume_graph_match", "main"):
            try:
                mod = importlib.import_module(name)
                return mod
            except ImportError:
                continue
    return None


_GRAPH_MATCH_MODULE = None  # 延迟加载


def _get_graph_match():
    """获取 resume-graph-match 模块（延迟加载单例）。"""
    global _GRAPH_MATCH_MODULE
    if _GRAPH_MATCH_MODULE is None:
        _GRAPH_MATCH_MODULE = _find_graph_match_module()
    return _GRAPH_MATCH_MODULE


def graph_match_available() -> bool:
    """resume-graph-match 模块是否已加载。"""
    return _get_graph_match() is not None


# ==================== 图匹配接口 ====================

def graph_similarity(
    candidate_five_dim: Dict[str, Any],
    jd_list: Sequence[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    """基于 Neo4j 图嵌入的相似度匹配（委托 resume-graph-match）。

    若 resume-graph-match 不可用，返回 None（上层回退到纯文本打分）。

    Args:
        candidate_five_dim: 候选人六维画像
        jd_list: JD 列表（至少含 job_title、five_dim、source 字段）

    Returns:
        排序后的匹配列表，或 None。
    """
    mod = _get_graph_match()
    if mod is None:
        return None

    # 尝试调用 resume-graph-match 的匹配函数
    for func_name in ("graph_match_jds", "match_resume_to_jds", "embedding_match", "run"):
        func = getattr(mod, func_name, None)
        if callable(func):
            try:
                result = func(candidate_five_dim, jd_list)
                if isinstance(result, list):
                    return result
            except Exception as exc:
                print(f"  [graph_match] {func_name}() 调用失败: {exc}")
                return None
    return None


def graph_path_score(
    resume_id: str,
    jd_node_id: str,
) -> Optional[float]:
    """计算简历与 JD 在图中的路径关联度（共同技能 / 路径距离）。

    图谱结构: JD-[:MENTIONS_NORMALIZED_SKILL]->NormalizedSkill
               JD-[:INSTANCE_OF]->Role

    Args:
        resume_id: 简历 Neo4j 节点 ID。
        jd_node_id: JD Neo4j 节点 ID。

    Returns:
        关联度得分 [0, 1]，或 None。
    """
    mod = _get_graph_match()
    if mod is not None:
        func = getattr(mod, "path_score", None)
        if callable(func):
            try:
                return func(resume_id, jd_node_id)
            except Exception as exc:
                print(f"  [graph_match] path_score() 失败: {exc}")

    # 若无 resume-graph-match，直接用 Neo4j 计算：共同技能数 + Role 家族距离
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        if not password:
            return None

        # Resume 节点与 JD 通过 NormalizedSkill 关联：
        # 若 Resume 节点尚未定义（当前图谱没有），直接返回 None
        cypher = """
            MATCH (r:Resume {resume_id: $resume_id})
            WITH r
            MATCH (jd:JD)
            WHERE toString(id(jd)) = $jd_id
            OPTIONAL MATCH (r)-[:MENTIONS_NORMALIZED_SKILL]->(s:NormalizedSkill)<-[:MENTIONS_NORMALIZED_SKILL]-(jd)
            WITH count(DISTINCT s) AS common_skills
            RETURN common_skills
        """

        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session(database=database) as session:
                result = session.run(cypher, {"resume_id": resume_id, "jd_id": jd_node_id})
                record = result.single()
                if record is None:
                    return None
                common = record.get("common_skills", 0) or 0
                # 共同技能越多，匹配度越高；归一化到 [0, 1]
                return round(min(common / 10.0, 1.0), 4)
        finally:
            driver.close()
    except Exception as exc:
        print(f"  [graph_match] Neo4j path_score 查询失败: {exc}")

    return None


# ==================== 混合排序 ====================

def hybrid_rank(
    candidate_five_dim: Dict[str, Any],
    jd_list: Sequence[Dict[str, Any]],
    topk: Optional[int] = None,
    weights: Optional[Dict[str, float]] = None,
    graph_weight: float = 0.3,
) -> List[Dict[str, Any]]:
    """混合排序：六维文本打分 + 图匹配得分加权融合。

    当 resume-graph-match 或 Neo4j 不可用时，自动回退到纯文本打分。

    Args:
        candidate_five_dim: 候选人六维画像
        jd_list: JD 列表
        topk: 返回前 N 名
        weights: 六维权重
        graph_weight: 图匹配得分的融合权重 [0, 1]，0=纯文本，1=纯图匹配

    Returns:
        排序后的匹配结果列表。
    """
    from src.retrieval.scoring import rank_jds

    # 1. 文本六维打分
    text_ranked = rank_jds(candidate_five_dim, jd_list, topk=None, weights=weights)

    # 2. 尝试图匹配
    graph_result = graph_similarity(candidate_five_dim, jd_list)

    if graph_result and graph_weight > 0:
        # 构建图得分索引
        graph_scores: Dict[str, float] = {}
        for item in graph_result:
            key = item.get("job_title", "") + item.get("source", "")
            graph_scores[key] = item.get("total_score", 0.0)

        # 融合得分
        for item in text_ranked:
            key = item.get("job_title", "") + item.get("source", "")
            gs = graph_scores.get(key, 0.0)
            item["text_score"] = item["total_score"]
            item["graph_score"] = gs
            item["total_score"] = round(
                (1 - graph_weight) * item["text_score"] + graph_weight * gs, 4
            )

        # 重新按融合得分排序
        text_ranked.sort(key=lambda x: x["total_score"], reverse=True)
    else:
        # 无图匹配，标记来源
        for item in text_ranked:
            item["text_score"] = item["total_score"]
            item["graph_score"] = None

    if topk and topk > 0:
        return text_ranked[:topk]
    return text_ranked


# ==================== 图谱统计 ====================

def get_graph_stats() -> Optional[Dict[str, Any]]:
    """获取 Neo4j 图数据库统计信息（适配 resume-handoff 图谱）。

    Returns:
        {"jd_count": int, "role_count": int, "skill_count": int, ...} 或 None
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        database = os.getenv("NEO4J_DATABASE", "neo4j")

        if not password:
            return None

        cypher = """
            MATCH (jd:JD) WITH count(jd) AS jd_count
            OPTIONAL MATCH (r:Role) WITH jd_count, count(r) AS role_count
            OPTIONAL MATCH (s:NormalizedSkill) WITH jd_count, role_count, count(s) AS skill_count
            RETURN jd_count, role_count, skill_count
        """

        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session(database=database) as session:
                result = session.run(cypher)
                record = result.single()
                if record:
                    return {
                        "jd_count": record.get("jd_count", 0),
                        "role_count": record.get("role_count", 0),
                        "skill_count": record.get("skill_count", 0),
                    }
        finally:
            driver.close()
    except Exception as exc:
        print(f"  [graph_match] 图谱统计查询失败: {exc}")

    return None
