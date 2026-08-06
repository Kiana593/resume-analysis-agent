"""CSV 数据源：读取"提取后原始数据"中的完整六维能力分析结果。

背景：图谱 JD 节点只存了 12 个字段，六维技能通过 MENTIONS_NORMALIZED_SKILL
关系关联且稀疏（平均 5.6 条）。而原始 CSV 的"能力分析结果"列保存了
LLM 提取的完整六维数据（每行约 15-20 条），信息量充足。

本模块：
    1. 扫描 CSV 目录，建立 source_file 相对路径 → 文件 的索引
    2. 解析"能力分析结果"列 → 六维 dict
    3. 用 CSV 的学历/经验列补充 qualifications 维度
    4. 提供图谱 JD → CSV 完整行的匹配（source_file + 职位描述）
"""

import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.retrieval.scoring import DIMENSION_KEYS

# CSV "能力分析结果"列里出现的维度前缀 → 六维 key
_CSV_DIM_ALIASES = {
    "知识": "knowledge",
    "技术": "skill",
    "任职条件": "qualifications",
    "招聘偏好": "qualifications",
    "动机": "motivation",
    "特质": "trait",
    "自我概念": "self_concept",
}

# 文件级缓存：{绝对路径: {"entries": [...], "by_desc": {norm_desc: entry}, "by_title": {norm_title: entry}}}
_file_cache: Dict[Path, Dict[str, Any]] = {}
_file_index: Dict[str, Path] = {}  # 相对路径 → 文件路径


# ==================== 目录扫描 ====================

def build_csv_index(csv_dir: str) -> Dict[str, Path]:
    """扫描 CSV 目录，建立相对路径索引。

    图谱 JD.source_file 是相对路径（如"半导体_output_folder/芯片测试工程师.csv"），
    本索引用同样格式的 key，实现图谱 JD → CSV 文件的定位。

    Args:
        csv_dir: 提取后原始数据目录。

    Returns:
        {相对路径(含_output_folder): 绝对路径}
    """
    global _file_index
    root = Path(csv_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"CSV 目录不存在: {root}")

    index: Dict[str, Path] = {}
    for path in root.rglob("*.csv"):
        rel = str(path.relative_to(root)).replace("\\", "/")
        index[rel] = path
    _file_index = index
    return index


def get_csv_files(csv_dir: str) -> Dict[str, Path]:
    """获取 CSV 文件索引（带缓存）。"""
    if not _file_index:
        build_csv_index(csv_dir)
    return _file_index


# ==================== 能力分析结果解析 ====================

def parse_capacity_result(text: str) -> Dict[str, List[str]]:
    """解析"能力分析结果"列 → 六维 dict。

    输入格式（各行一个维度）：
        知识：自动化、计算机科学学位；机器学习理论基础
        技术：深度学习框架熟练；Python/C++编程熟练
        动机：关注行业动态
        特质：解决问题能力强
        自我概念：团队合作意识

    Args:
        text: 能力分析结果原文。

    Returns:
        {dim: [item, ...]}，dim ∈ DIMENSION_KEYS。
    """
    five_dim: Dict[str, List[str]] = {dim: [] for dim in DIMENSION_KEYS}
    if not text:
        return five_dim

    # 按行拆分，每行匹配 "维度名：内容"（允许行首可选序号："1. 知识：..."）
    # 无意义占位条目（"无"、"无明确描述"、"无（原文未提及）"）直接跳过
    _DIM_LINE = re.compile(r"^\s*(?:\d+[.、．]\s*)?([\u4e00-\u9fff]{2,6})\s*[:：]\s*(.+)$")
    # 过滤无意义占位条目：兼容 "无"、"无明确描述"、"（无明确描述）"、"无（原文未提及）" 等形态
    _USELESS = re.compile(r"^(?:无|暂无|未提及|无明确描述|N/?A|无[（(].*[)）]|[（(]无[^）)]*[)）])")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _DIM_LINE.match(line)
        if not m:
            continue
        dim_alias = m.group(1)
        dim = _CSV_DIM_ALIASES.get(dim_alias)
        if not dim:
            continue
        body = m.group(2)
        items = re.split(r"[;；,，。]+", body)
        seen = set(five_dim[dim])
        for item in items:
            item = item.strip()
            if not item or _USELESS.match(item) or item in seen:
                continue
            five_dim[dim].append(item)
            seen.add(item)
    return five_dim


# ==================== CSV 文件加载 ====================

def _norm_text(text: str) -> str:
    """归一化文本用于匹配（去空白、大小写）。"""
    if not text:
        return ""
    return re.sub(r"\s+", "", text).lower()


def load_csv_file(file_path: Path) -> Dict[str, Any]:
    """加载单个 CSV 文件（缓存），建立描述/标题索引。

    Args:
        file_path: CSV 绝对路径。

    Returns:
        {
            "entries": [每行一条],
            "by_desc": {norm_description: entry},
            "by_title": {norm_title: entry},
        }
    """
    if file_path in _file_cache:
        return _file_cache[file_path]

    # 反查相对路径（一次）
    rel = _rel_path_of(file_path)

    entries: List[Dict[str, Any]] = []
    by_desc: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}

    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [h.lstrip("\ufeff") if h else h for h in (reader.fieldnames or [])]
            if not reader.fieldnames:
                cached = {"entries": [], "by_desc": {}, "by_title": {}}
                _file_cache[file_path] = cached
                return cached

            for row in reader:
                entry = _row_to_entry(row, rel)
                if entry is None:
                    continue
                entries.append(entry)
                norm_desc = _norm_text(entry.get("description", ""))
                norm_title = _norm_text(entry.get("job_title", ""))
                if norm_desc and norm_desc not in by_desc:
                    by_desc[norm_desc] = entry
                if norm_title and norm_title not in by_title:
                    by_title[norm_title] = entry
    except Exception as exc:
        print(f"  [csv] 读取失败 {file_path}: {exc}")

    cached = {"entries": entries, "by_desc": by_desc, "by_title": by_title}
    _file_cache[file_path] = cached
    return cached


def _row_to_entry(row: Dict[str, Any], rel_path: str) -> Optional[Dict[str, Any]]:
    """单行 CSV → 标准条目。"""
    def get(col: str) -> str:
        val = row.get(col)
        if val is None:
            return ""
        if isinstance(val, str):
            return val.strip()
        return str(val).strip()

    capacity = get("能力分析结果")
    if not capacity:
        return None

    five_dim = parse_capacity_result(capacity)

    # 用学历/经验列补充 qualifications（若该维度条目少）
    edu = get("学历要求")
    exp = get("经验要求")
    if edu and len(five_dim.get("qualifications", [])) < 3:
        five_dim["qualifications"].append(edu)
    if exp and len(five_dim.get("qualifications", [])) < 4:
        five_dim["qualifications"].append(exp)

    return {
        "source_file": rel_path,
        "job_title": get("职位名称"),
        "company_name": get("公司全称"),
        "description": get("职位描述"),
        "education": edu,
        "experience": exp,
        "five_dim": five_dim,
    }


def _rel_path_of(file_path: Path) -> str:
    """从文件索引反查相对路径（如"半导体_output_folder/芯片测试工程师.csv"）。"""
    for rel, p in _file_index.items():
        if p.resolve() == file_path.resolve():
            return rel
    return file_path.name


# ==================== 图谱 JD → CSV 匹配 ====================

def resolve_jd_to_csv(
    jd: Dict[str, Any],
    csv_dir: str,
) -> Optional[Dict[str, Any]]:
    """将图谱 JD 匹配到 CSV 完整行（source_file + 职位描述，O(1) 索引查找）。

    匹配策略（按优先级）：
        1. source_file 相同 + 归一化 description 完全一致
        2. source_file 相同 + title 一致
        3. source_file 相同（取第一条）

    Args:
        jd: 图谱 JD 条目（含 source_file, description, title）。
        csv_dir: CSV 数据目录。

    Returns:
        CSV 完整条目（含 five_dim），无匹配返回 None。
    """
    get_csv_files(csv_dir)
    source_file = jd.get("source_file", "")
    if not source_file or source_file.startswith("neo4j://"):
        return None

    # 定位文件：source_file 可能带或不带 _output_folder 前缀
    candidates: List[Path] = []
    direct = _file_index.get(source_file)
    if direct:
        candidates.append(direct)
    else:
        for rel, p in _file_index.items():
            if rel.endswith(source_file) or source_file.endswith(rel):
                candidates.append(p)
    if not candidates:
        return None

    jd_desc = _norm_text(jd.get("description", ""))
    jd_title = _norm_text(jd.get("title", ""))

    # 精确描述匹配（优先，O(1)）
    for path in candidates:
        cached = load_csv_file(path)
        if jd_desc and jd_desc in cached["by_desc"]:
            return cached["by_desc"][jd_desc]
    # 标题匹配
    for path in candidates:
        cached = load_csv_file(path)
        if jd_title and jd_title in cached["by_title"]:
            return cached["by_title"][jd_title]
    # 兜底：第一条
    for path in candidates:
        cached = load_csv_file(path)
        if cached["entries"]:
            return cached["entries"][0]
    return None


# ==================== 批量匹配 ====================

def enrich_jds_with_csv(
    jds: List[Dict[str, Any]],
    csv_dir: str,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """批量将图谱 JD 增强为 CSV 完整六维数据。

    Args:
        jds: 图谱 JD 列表（含 source_file, description, title, five_dim...）。
        csv_dir: CSV 数据目录。

    Returns:
        (增强后的 JD 列表, 成功匹配数, 总数)。
        增强后 five_dim 优先用 CSV 完整数据；无匹配保留图谱数据。
    """
    get_csv_files(csv_dir)
    matched = 0
    for jd in jds:
        csv_entry = resolve_jd_to_csv(jd, csv_dir)
        if csv_entry is not None:
            jd["five_dim"] = csv_entry["five_dim"]
            jd["company_name"] = csv_entry.get("company_name", jd.get("company_name", ""))
            jd["raw_text"] = csv_entry.get("description", jd.get("raw_text", ""))
            matched += 1
    return jds, matched, len(jds)
