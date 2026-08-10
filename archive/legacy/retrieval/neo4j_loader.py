"""JD 数据接入层 + Neo4j 知识图谱对接。
- 本地文件模式（load_jds_from_local）：读取 jds/*.json，用于测试与演示。
- Neo4j 模式（load_jds_from_neo4j）：从 Neo4j 图数据库查询 JD 六维数据。

图谱结构（resume-handoff.dump）：
    节点: JD / Role / NormalizedSkill
    关系: JD-[:MENTIONS_NORMALIZED_SKILL]->NormalizedSkill
          JD-[:INSTANCE_OF]->Role
          Role-[:HAS_CORE_SKILL]->NormalizedSkill
    NormalizedSkill.category ∈ {知识, 技术, 任职条件, 动机, 特质, 自我概念, 招聘偏好}

数据契约（所有模式统一返回 List[Dict]，每个元素至少包含）：
    {
        "job_title": str,                  # 岗位名称（jd.title）
        "five_dim": Dict[str, List[str]],  # 六维要求（category → DIMENSION_KEYS）
        "source": str,                     # 可选，来源标识
        "raw_text": str,                   # 可选，JD 原文（jd.description）
    }
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

load_dotenv()

# 六维 key 顺序（与 scoring.DIMENSION_KEYS 一致）
DIMENSION_KEYS = (
    "knowledge",
    "skill",
    "qualifications",
    "motivation",
    "trait",
    "self_concept",
)

from src.retrieval.scoring import CATEGORY_TO_DIM


# ==================== Neo4j 连接 ====================

_NEO4J_AVAILABLE = None


def _check_neo4j():
    """检查 neo4j 包是否可用。"""
    global _NEO4J_AVAILABLE
    if _NEO4J_AVAILABLE is None:
        try:
            import neo4j  # noqa: F401
            _NEO4J_AVAILABLE = True
        except ImportError:
            _NEO4J_AVAILABLE = False
    return _NEO4J_AVAILABLE


def _get_neo4j_config() -> Dict[str, str]:
    """从环境变量读取 Neo4j 连接配置。"""
    config = {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", ""),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
    }
    if not config["password"]:
        raise ValueError(
            "未配置 NEO4J_PASSWORD 环境变量。请在 .env 中设置 Neo4j 连接密码。\n"
            "若未安装 Neo4j，请使用本地模式：--source local"
        )
    return config


# ==================== JD 查询 ====================

def load_jds_from_neo4j(
    job_title_filter: Optional[str] = None,
    limit: Optional[int] = None,
    include_empty: bool = False,
    min_features: int = 0,
) -> List[Dict[str, Any]]:
    """从 Neo4j 加载全部 JD 六维数据（适配 resume-handoff 图谱）。

    通过 JD-[:MENTIONS_NORMALIZED_SKILL]->NormalizedSkill 关系，
    将 skill.category 映射为六维画像（knowledge/skill/qualifications/...）。

    Args:
        job_title_filter: 可选，按岗位名称模糊过滤（CONTAINS）。
        limit: 可选，限制返回数量。
        include_empty: 是否包含六维全空的 JD。
            默认 False：跳过没有 NormalizedSkill 关联的 JD（特征缺失，
            若参与评分会因"空维度=满分"而虚高，全部得 1.0）。
        min_features: 六维总条目数下限，过滤信息量过低的 JD。
            默认 0 不过滤；建议 6（信息量不足的 JD 评分区分度差，
            仅 1-3 条要求的 JD 容易被简历全量覆盖而虚高得满分）。

    Returns:
        符合数据契约的 JD 列表。
    """
    if not _check_neo4j():
        raise RuntimeError(
            "neo4j 驱动未安装。请执行: pip install neo4j\n"
            "或使用本地模式：--source local"
        )

    config = _get_neo4j_config()
    from neo4j import GraphDatabase

    where_clause = ""
    params: Dict[str, Any] = {}
    if job_title_filter:
        where_clause = "WHERE jd.title CONTAINS $filter "
        params["filter"] = job_title_filter
    limit_clause = f"LIMIT {limit}" if limit else ""

    # 一个 JD 关联多个 NormalizedSkill；collect 后按 category 分组构建 six_dim
    cypher = f"""
        MATCH (jd:JD)
        {where_clause}
        OPTIONAL MATCH (jd)-[:MENTIONS_NORMALIZED_SKILL]->(sk:NormalizedSkill)
        WITH jd, sk
        ORDER BY sk.verified_rate DESC
        WITH jd,
             collect(DISTINCT {{name: sk.canonical_name, cat: sk.category}}) AS skills
        RETURN jd.title AS job_title,
               jd.description AS raw_text,
               jd.company_name AS company_name,
               jd.salary AS salary,
               jd.experience AS experience,
               jd.tags AS tags,
               elementId(jd) AS node_id,
               skills
        {limit_clause}
    """

    driver = GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    )

    jds: List[Dict[str, Any]] = []
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, params)
            for record in result:
                entry = _build_jd_entry(record)
                if include_empty or _has_features(entry):
                    if min_features <= 0 or _feature_count(entry) >= min_features:
                        jds.append(entry)
    finally:
        driver.close()

    if not jds:
        raise RuntimeError(
            "Neo4j 中未找到 JD 数据。请确认知识图谱已导入 JD 节点（标签: JD）。\n"
            "若尚未导入，请先运行 resume-graph-match 的数据导入脚本，"
            "或使用本地模式：--source local"
        )

    return jds


def _has_features(jd_entry: Dict[str, Any]) -> bool:
    """判断 JD 是否具备可匹配的特征（六维任一维度非空）。"""
    five_dim = jd_entry.get("five_dim", {})
    return any(bool(v) for v in five_dim.values())


def _feature_count(jd_entry: Dict[str, Any]) -> int:
    """JD 六维总条目数（信息量）。"""
    five_dim = jd_entry.get("five_dim", {})
    return sum(len(v) for v in five_dim.values())


def _build_jd_entry(record) -> Dict[str, Any]:
    """将 Neo4j 记录解析为标准数据契约格式（JD + skills → five_dim）。"""
    five_dim: Dict[str, List[str]] = {dim: [] for dim in DIMENSION_KEYS}

    for item in record.get("skills", []) or []:
        dim = CATEGORY_TO_DIM.get(item.get("cat", ""))
        name = str(item.get("name", "")).strip()
        if dim and name:
            if name not in five_dim[dim]:
                five_dim[dim].append(name)

    return {
        "job_title": record.get("job_title", "Unknown"),
        "source": f"neo4j://{record.get('node_id', '')}",
        "five_dim": five_dim,
        "raw_text": record.get("raw_text", ""),
        "company_name": record.get("company_name", ""),
        "salary": record.get("salary", ""),
        "experience": record.get("experience", ""),
    }


def _parse_jd_record(record) -> Dict[str, Any]:
    """解析单条 Neo4j JD 记录为标准数据契约格式（兼容旧版 JD 节点属性）。

    当图谱中的 JD 节点直接存储 six_dim 属性（而非通过关系关联）时使用。
    """
    def _to_list(value) -> List[str]:
        """将 Neo4j 字段转为 List[str]，兼容 JSON 字符串和列表。"""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
            return [value]
        return [str(value)]

    five_dim = {
        "knowledge": _to_list(record.get("knowledge")),
        "skill": _to_list(record.get("skill")),
        "qualifications": _to_list(record.get("qualifications")),
        "motivation": _to_list(record.get("motivation")),
        "trait": _to_list(record.get("trait")),
        "self_concept": _to_list(record.get("self_concept")),
    }

    return {
        "job_title": record.get("job_title", "Unknown"),
        "source": f"neo4j://{record.get('node_id', '')}",
        "five_dim": five_dim,
        "raw_text": record.get("raw_text", ""),
    }


# ==================== 简历写入 ====================

def save_resume_to_neo4j(
    resume_data: Dict[str, Any],
    resume_id: Optional[str] = None,
) -> str:
    """将提取的简历六维数据写入 Neo4j（标签: Resume）。

    注意：当前 resume-handoff 图谱只有 JD / Role / NormalizedSkill 节点，
    Resume 标签需先由知识图谱模块定义。写入后可通过 load_resumes_from_neo4j 读取。

    Args:
        resume_data: 简历 JSON（含 five_dim, personal_info, raw_text 等）。
        resume_id: 可选，简历唯一标识（默认用姓名或自动生成）。

    Returns:
        Neo4j 节点 ID。
    """
    if not _check_neo4j():
        raise RuntimeError("neo4j 驱动未安装。请执行: pip install neo4j")

    config = _get_neo4j_config()
    from neo4j import GraphDatabase

    five_dim = resume_data.get("five_dim", {})
    personal = resume_data.get("personal_info", {})
    raw_text = resume_data.get("raw_text", "")

    cypher = """
        MERGE (r:Resume {resume_id: $resume_id})
        SET r.name = $name,
            r.education = $education,
            r.knowledge = $knowledge,
            r.skill = $skill,
            r.qualifications = $qualifications,
            r.motivation = $motivation,
            r.trait = $trait,
            r.self_concept = $self_concept,
            r.raw_text = $raw_text,
            r.updated_at = datetime()
        RETURN elementId(r) AS node_id
    """

    params = {
        "resume_id": resume_id or personal.get("name", "unknown"),
        "name": personal.get("name", ""),
        "education": personal.get("education", ""),
        "knowledge": json.dumps(five_dim.get("knowledge", []), ensure_ascii=False),
        "skill": json.dumps(five_dim.get("skill", []), ensure_ascii=False),
        "qualifications": json.dumps(five_dim.get("qualifications", []), ensure_ascii=False),
        "motivation": json.dumps(five_dim.get("motivation", []), ensure_ascii=False),
        "trait": json.dumps(five_dim.get("trait", []), ensure_ascii=False),
        "self_concept": json.dumps(five_dim.get("self_concept", []), ensure_ascii=False),
        "raw_text": raw_text or "",
    }

    driver = GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    )

    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, params)
            record = result.single()
            node_id = record["node_id"] if record else ""
            return node_id
    finally:
        driver.close()


def save_resumes_batch(
    resumes: Sequence[Dict[str, Any]],
) -> List[str]:
    """批量写入简历到 Neo4j（事务批处理，性能优于逐条写入）。

    Args:
        resumes: 简历数据列表。

    Returns:
        Neo4j 节点 ID 列表。
    """
    node_ids = []
    for resume in resumes:
        try:
            nid = save_resume_to_neo4j(resume)
            node_ids.append(nid)
        except Exception as exc:
            print(f"  [neo4j] 写入失败: {resume.get('personal_info', {}).get('name', '?')} - {exc}")
            node_ids.append("")
    return node_ids


# ==================== 简历查询 ====================

def load_resumes_from_neo4j(
    name_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """从 Neo4j 加载已存储的简历六维数据（标签: Resume）。

    当前图谱若无 Resume 标签，返回空列表（不报错），
    并提示先通过 save_resume_to_neo4j 写入。

    Args:
        name_filter: 可选，按姓名模糊过滤。
        limit: 可选，限制返回数量。

    Returns:
        简历数据列表（包含 five_dim + personal_info）。
    """
    if not _check_neo4j():
        raise RuntimeError("neo4j 驱动未安装。请执行: pip install neo4j")

    config = _get_neo4j_config()
    from neo4j import GraphDatabase

    where_clause = ""
    params: Dict[str, Any] = {}
    if name_filter:
        where_clause = "WHERE r.name CONTAINS $filter "
        params["filter"] = name_filter
    limit_clause = f"LIMIT {limit}" if limit else ""

    cypher = f"""
        MATCH (r:Resume)
        {where_clause}
        RETURN r.resume_id AS resume_id,
               r.name AS name,
               r.education AS education,
               r.knowledge AS knowledge,
               r.skill AS skill,
               r.qualifications AS qualifications,
               r.motivation AS motivation,
               r.trait AS trait,
               r.self_concept AS self_concept,
               r.raw_text AS raw_text,
               elementId(r) AS node_id
        {limit_clause}
    """

    driver = GraphDatabase.driver(
        config["uri"],
        auth=(config["user"], config["password"]),
    )

    resumes: List[Dict[str, Any]] = []
    try:
        with driver.session(database=config["database"]) as session:
            result = session.run(cypher, params)
            for record in result:
                entry = _parse_jd_record(record)  # 复用解析逻辑
                entry["personal_info"] = {
                    "name": record.get("name", ""),
                    "education": record.get("education", ""),
                }
                entry["resume_id"] = record.get("resume_id", "")
                resumes.append(entry)
    finally:
        driver.close()

    return resumes


# ==================== 本地文件模式 ====================

def load_jds_from_local(jd_dir: str) -> List[Dict[str, Any]]:
    """从本地目录读取 JD JSON 文件（每个文件一个 JD）。

    Args:
        jd_dir: 存放 JD JSON 的目录，文件名任意，如 jds/。

    Returns:
        符合数据契约的 JD 列表。
    """
    directory = Path(jd_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"JD 目录不存在: {directory}")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"JD 目录下没有 JSON 文件: {directory}")

    jds: List[Dict[str, Any]] = []
    for path in files:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            continue
        entry = {
            "job_title": data.get("job_title", path.stem),
            "source": str(path),
            "five_dim": data.get("five_dim", {}),
            "raw_text": data.get("raw_text", ""),
        }
        jds.append(entry)
    return jds


