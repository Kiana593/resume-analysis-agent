"""六维加权打分 —— 阶段1 初筛（非 LLM，纯算法）。

职责：
- 输入候选人六维画像 + JD 六维要求列表，输出匹配度打分与排序。
- 与数据来源解耦：打分函数只接收标准的 five_dim 结构，JD 数据既可来自本地
  JSON 文件，也可来自 Neo4j（见 neo4j_loader.py，由知识图谱模块负责实现）。

数据契约（five_dim）：
    Dict[str, List[str]]，key 限定为六维：
        knowledge / skill / qualifications / motivation / trait / self_concept

维度得分语义（dim_coverage）：
    该维度 JD 要求条目中被候选人覆盖的比例：
        score = 被覆盖的 JD 条目数 / JD 条目总数
    JD 该维度无要求（空列表）时视为无差距，得 1.0。

总分（score_jd）：
    total = Σ(weight[dim] * dim_score[dim])，权重默认：
        knowledge 0.25 / skill 0.25 / qualifications 0.20
        motivation 0.10 / trait 0.10 / self_concept 0.10
"""

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

DIMENSION_KEYS = (
    "knowledge",
    "skill",
    "qualifications",
    "preference",
    "motivation",
    "trait",
    "self_concept",
)

# NormalizedSkill.category（图谱技能类别）→ 六维画像 key
CATEGORY_TO_DIM = {
    "知识": "knowledge",
    "技术": "skill",
    "任职条件": "qualifications",
    "招聘偏好": "preference",
    "动机": "motivation",
    "特质": "trait",
    "自我概念": "self_concept",
}

# 默认权重：知识/技术/任职条件是硬性门槛（与 LLM 判定规则一致），权重高；
# 动机/特质/自我概念为软性要求，权重低。可通过 weights 参数覆盖（内部自动归一化）。
DEFAULT_WEIGHTS: Dict[str, float] = {
    "knowledge": 0.22,
    "skill": 0.22,
    "qualifications": 0.18,
    "preference": 0.10,
    "motivation": 0.10,
    "trait": 0.09,
    "self_concept": 0.09,
}

# JD 层少条目惩罚阈值：JD 六维要求条目总数少于该值时，
# 总分为覆盖率 × (要求条目数 / K)，防止图谱稀疏数据（仅 2-5 条技能）虚高。
JD_MIN_FEATURES_K = 12

# 条目匹配参数
_MIN_SUBSTR_LEN = 3          # 归一化后短语子串匹配的最短长度（中文 3 字符 ≈ 1 词）
_MIN_PHRASE_LEN = 4          # 滑窗直接命中长度（>=4 的连续子串视为可靠短语）
_MIN_CONTAIN_LEN = 3         # 整串包含匹配的最短长度
_MIN_EN_SUBSTR_LEN = 4       # 纯英文连续字母串的子串最短长度，避免 "ai"/"ic" 误命中
_OVERLAP_THRESHOLD = 0.75    # 字符重叠率兜底阈值

# 3 字符窗口命中时的通用后缀/前缀词黑名单：
# 避免 "博士学历" vs "本科硕士学历" 因共享 "士学历" 而误判命中
_GENERIC_WINDOW_WORDS = (
    "学历", "经验", "能力", "知识", "意识", "精神", "专业", "原理", "相关",
    "以上", "良好", "熟练", "熟悉", "掌握", "精通", "工程", "技术", "设计",
    "开发", "流程", "要求", "条件", "优先", "加分", "方向", "背景",
)

# 学历等级（用于任职条件特判：等级比较而非词法匹配）
_DEGREE_LEVELS = {"博士": 3, "硕士": 2, "研究生": 2, "本科": 1, "学士": 1, "大专": 0, "专科": 0}


# ==================== 原文技能命中搜索 ====================

def match_skills_in_text(raw_text, skills):
    """在简历原文中搜索 Role 技能命中（归一化子串匹配）。

    Args:
        raw_text: 简历 Markdown 原文
        skills: Role 技能列表 [{name, category, weight, rank}, ...]

    Returns:
        {"hit": [...], "miss": [...], "hit_count": N, "total": M,
         "by_dim": {dim: {"hit": [...], "miss": [...], "hit_count": N, "total": M}}}
    """
    norm_text = _normalize(raw_text)
    hits = []
    misses = []
    by_dim = {}

    for sk in skills:
        name = sk.get("name", "").strip()
        if not name:
            continue
        dim = CATEGORY_TO_DIM.get(sk.get("category", ""))
        if not dim:
            continue
        by_dim.setdefault(dim, {"hit": [], "miss": [], "hit_count": 0, "total": 0})

        norm_name = _normalize(name)
        matched = norm_name in norm_text if len(norm_name) >= 2 else False

        # 找到原文中的位置（用于高亮）
        positions = []
        if matched:
            start = 0
            while True:
                idx = raw_text.lower().find(name.lower(), start)
                if idx == -1:
                    break
                positions.append((idx, idx + len(name)))
                start = idx + 1

        entry = {"name": name, "category": sk.get("category", ""), "dim": dim}
        if matched:
            entry["positions"] = positions
            hits.append(entry)
            by_dim[dim]["hit"].append(entry)
            by_dim[dim]["hit_count"] += 1
        else:
            misses.append(entry)
            by_dim[dim]["miss"].append(entry)
        by_dim[dim]["total"] += 1

    return {
        "hit": hits, "miss": misses,
        "hit_count": len(hits), "total": len(skills),
        "by_dim": by_dim,
    }


def _normalize(text: str) -> str:
    """归一化：去空白、常见标点、全角转半角、转小写。"""
    t = text or ""
    t = t.replace("\u3000", " ").replace("\xa0", " ")
    # 全角转半角
    t = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in t)
    t = re.sub(r"[\s，。；、,.!?！？:：;；()（）\[\]【】/\\|~`·\-—_]+", "", t)
    return t.lower()


def _max_degree_level(text: str) -> int:
    """提取文本中的最高学历等级（博士3 > 硕士2 > 本科1 > 大专0，无则 -1）。"""
    level = -1
    for word, lv in _DEGREE_LEVELS.items():
        if word in text and lv > level:
            level = lv
    return level


def _degree_satisfied(cand: str, jd: str) -> bool:
    """任职条件学历特判：按等级比较，而非词法匹配。

    如 "本科及以上学历" vs "本科/硕士学历" → 满足；
    "博士学历" vs "本科/硕士学历" → 不满足。
    """
    cand_level = _max_degree_level(cand)
    jd_level = _max_degree_level(jd)
    if cand_level < 0 or jd_level < 0:
        return False
    if "及以上" in jd or "以上" in jd:
        return cand_level >= jd_level
    if "以下" in jd or "及以下" in jd:
        return cand_level <= jd_level
    return cand_level >= jd_level


def _item_match(candidate_item: str, jd_item: str) -> bool:
    """判断候选人条目是否覆盖 JD 条目（多级模糊匹配）。"""
    cand = _normalize(candidate_item)
    jd = _normalize(jd_item)
    if not cand or not jd:
        return False
    if cand == jd:
        return True
    # 1. 整串包含（浓缩摘录："模拟IC设计经验" ⊂ "5年以上模拟IC设计经验"）
    if len(cand) >= _MIN_CONTAIN_LEN and cand in jd:
        return True
    if len(jd) >= _MIN_CONTAIN_LEN and jd in cand:
        return True
    # 学历硬性否决：双方均含学历词但等级不满足 → 直接不匹配（如 硕士 vs 博士）
    if (
        _max_degree_level(cand) >= 0
        and _max_degree_level(jd) >= 0
        and not _degree_satisfied(cand, jd)
    ):
        return False
    shorter, longer = (cand, jd) if len(cand) <= len(jd) else (jd, cand)
    # 2. 短语滑窗：>=4 字符的连续子串命中 → 可靠短语
    for start in range(len(shorter) - _MIN_PHRASE_LEN + 1):
        if shorter[start:start + _MIN_PHRASE_LEN] in longer:
            return True
    # 3. 短语滑窗：3 字符连续子串命中，且窗口不含通用词（防 "士学历" 类误报）
    for start in range(len(shorter) - _MIN_SUBSTR_LEN + 1):
        window = shorter[start:start + _MIN_SUBSTR_LEN]
        if window in longer and not any(g in window for g in _GENERIC_WINDOW_WORDS):
            return True
    # 4. 英文/数字 token（>=4）跨文本命中
    for token in re.findall(r"[a-z0-9]+", shorter):
        if len(token) >= _MIN_EN_SUBSTR_LEN and token in longer:
            return True
    # 5. 任职条件学历特判（等级比较）
    if _degree_satisfied(cand, jd):
        return True
    # 6. 字符重叠率兜底（按较短文本计，容忍浓缩表述）
    #    仅对中文/混合文本生效；纯英文/数字串需完整子串包含，
    #    避免 "fpga" 与 "figma" 因共享 f/g/a 字符而误判匹配。
    if len(shorter) >= _MIN_SUBSTR_LEN:
        if re.fullmatch(r"[a-z0-9]+", shorter):
            return shorter in longer
        overlap = sum(1 for ch in shorter if ch in longer)
        if overlap / len(shorter) >= _OVERLAP_THRESHOLD:
            return True
    return False


def _dim_stats(candidate_items: Sequence[str], jd_items: Sequence[str]) -> Tuple[int, int]:
    """统计单维度命中数：(matched_jd_count, jd_total)。"""
    jd_list = [str(i).strip() for i in jd_items or [] if str(i).strip()]
    cand_list = [str(i).strip() for i in candidate_items or [] if str(i).strip()]
    if not jd_list:
        return 0, 0
    if not cand_list:
        return 0, len(jd_list)
    matched = sum(1 for jd_item in jd_list if any(_item_match(c, jd_item) for c in cand_list))
    return matched, len(jd_list)


def dim_coverage(candidate_items: Sequence[str], jd_items: Sequence[str]) -> float:
    """单维度得分：JD 要求条目中被候选人覆盖的比例。

    JD 无要求（空列表）视为无差距，得 1.0；候选人为空则必为 0.0。
    """
    matched, total = _dim_stats(candidate_items, jd_items)
    return 1.0 if total == 0 else matched / total


def _normalize_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """校验并归一化权重（总和不必为 1，内部自动归一化）。"""
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    unknown = set(weights) - set(DIMENSION_KEYS)
    if unknown:
        raise ValueError(f"权重包含非法维度: {sorted(unknown)}（合法: {DIMENSION_KEYS}）")
    if not weights:
        raise ValueError("权重不能为空")
    if any(v < 0 for v in weights.values()):
        raise ValueError("权重不能为负")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("权重总和必须大于 0")
    full = {k: 0.0 for k in DIMENSION_KEYS}
    full.update(weights)
    return {k: v / total for k, v in full.items()}


def score_jd(
    candidate_five_dim: Dict[str, Any],
    jd_five_dim: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """计算候选人 vs 单个 JD 的六维加权得分。

    Args:
        candidate_five_dim: 候选人六维画像（{dim: [str]}，可含 personal_info 等冗余 key）
        jd_five_dim: JD 六维要求（{dim: [str]}）
        weights: 自定义权重（可选，自动归一化）

    Returns:
        {
            "total_score": float,
            "dim_scores": {dim: {"score": float, "jd_total": int, "jd_matched": int}},
        }
    """
    w = _normalize_weights(weights)
    dim_scores: Dict[str, Any] = {}
    total = 0.0
    for dim in DIMENSION_KEYS:
        jd_items = jd_five_dim.get(dim, []) if isinstance(jd_five_dim, dict) else []
        cand_items = candidate_five_dim.get(dim, []) if isinstance(candidate_five_dim, dict) else []
        matched, jd_total = _dim_stats(cand_items, jd_items)
        score = 1.0 if jd_total == 0 else matched / jd_total
        dim_scores[dim] = {
            "score": round(score, 4),
            "jd_total": jd_total,
            "jd_matched": matched,
        }
        total += w[dim] * score
    # JD 层少条目惩罚：要求条目过少（数据稀疏）时按比例打折，防止覆盖率虚高
    jd_req_total = sum(d["jd_total"] for d in dim_scores.values())
    if jd_req_total <= 0:
        penalty = 1.0
    else:
        penalty = min(1.0, jd_req_total / JD_MIN_FEATURES_K)
    return {
        "total_score": round(total * penalty, 4),
        "dim_scores": dim_scores,
        "jd_penalty": round(penalty, 4),
        "jd_req_total": jd_req_total,
    }


def rank_jds(
    candidate_five_dim: Dict[str, Any],
    jd_list: Sequence[Dict[str, Any]],
    topk: Optional[int] = None,
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """对多个 JD 打分并按总分降序排序。

    Args:
        candidate_five_dim: 候选人六维画像
        jd_list: JD 列表，每个元素至少含 job_title 与 five_dim
        topk: 只返回前 N 名（None 或 <=0 返回全部）
        weights: 自定义权重（可选）

    Returns:
        排序后的列表，每条：
        {
            "job_title": str, "source": str,
            "total_score": float, "dim_scores": {...},
        }
    """
    scored = []
    for jd in jd_list:
        five_dim = jd.get("five_dim", {}) if isinstance(jd, dict) else {}
        result = score_jd(candidate_five_dim, five_dim, weights=weights)
        scored.append(
            {
                "job_title": jd.get("job_title", ""),
                "source": jd.get("source", ""),
                **result,
            }
        )
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    if topk and topk > 0:
        scored = scored[:topk]
    return scored
