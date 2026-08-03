"""JD 数据接入层。

- 本地文件模式（load_jds_from_local）：读取 jds/*.json，用于测试与演示。
- Neo4j 模式（load_jds_from_neo4j）：**由知识图谱模块负责实现**，
  本文件只定义接口契约，当前调用会抛出 NotImplementedError。

数据契约（所有模式统一返回 List[Dict]，每个元素至少包含）：
    {
        "job_title": str,                  # 岗位名称
        "five_dim": Dict[str, List[str]],  # 六维要求（见 scoring.DIMENSION_KEYS）
        "source": str,                     # 可选，来源标识
        "raw_text": str,                   # 可选，JD 原文
    }
"""

import json
from pathlib import Path
from typing import Any, Dict, List


def load_jds_from_neo4j(*args, **kwargs) -> List[Dict[str, Any]]:
    """从 Neo4j 加载全部 JD 六维数据（待知识图谱模块实现）。

    Args:
        具体参数由知识图谱模块定义（如查询 Cypher、过滤条件、连接配置）。

    Returns:
        符合上方数据契约的 JD 列表。

    Raises:
        NotImplementedError: 当前版本未接入 Neo4j。
    """
    raise NotImplementedError(
        "Neo4j 接入由知识图谱模块负责。当前请使用本地模式："
        "python src/main.py rank -r <简历> -j <JD目录> --source local"
    )


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
