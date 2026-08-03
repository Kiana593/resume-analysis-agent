"""幻觉校验节点 —— 纯代码检查 LLM 输出：证据引用真实性 + 格式合规 + 逻辑一致性。

适配维度级输出结构（dimensions + extra_gaps + match）。
"""

import re

from src.state import AgentState

VALID_DIMENSIONS = {"knowledge", "skill", "qualifications", "motivation", "trait", "self_concept"}
VALID_GAP_LEVELS = {"missing", "partial", "sufficient"}
VALID_IMPORTANCE = {"high", "medium", "low"}

# 六维固定 key（校验 dimensions 必须齐全）
DIMENSION_KEYS = ("knowledge", "skill", "qualifications", "motivation", "trait", "self_concept")

# 硬性缺失维度：任一为 missing 则判定不匹配
HARD_MISS_DIMENSIONS = {"knowledge", "skill", "qualifications"}

# extra_gaps 数量上限
EXTRA_GAPS_LIMIT = 10

# "无" 及其变体视为合法的缺失标记
_MISSING_MARKERS = {"无", "无相关", "无相关内容", "未找到", "暂无", "none", "n/a", "na"}


def _normalize(text: str) -> str:
    """去除空白与省略号（.../…/。。），避免截断差异干扰匹配。"""
    t = re.sub(r"\s+", "", text or "")
    t = re.sub(r"\.{2,}|…|。。", "", t)
    return t


def _is_missing_marker(text: str) -> bool:
    return (
        _normalize(text).lower() in {_normalize(m).lower() for m in _MISSING_MARKERS}
        or not text.strip()
    )


def _evidence_matches(
    evidence: str,
    original: str,
    extra_sources: list[str] | None = None,
    min_chunk: int = 6,
    overlap_threshold: float = 0.7,
) -> bool:
    """检查文本是否能在原文中找到依据（多级模糊匹配）。

    容忍浓缩摘录（如"熟练EDA工具" vs "熟练使用Cadence、Synopsys等EDA工具"），
    同时拦截完全虚构的内容。
    """
    if _is_missing_marker(evidence):
        return True

    sources = [_normalize(original)] + [_normalize(s) for s in (extra_sources or [])]
    sources = [s for s in sources if s]

    ev = _normalize(evidence)
    if not ev:
        return True

    # 1. 整体子串（任一来源）
    for src in sources:
        if ev in src:
            return True

    # 2. 按标点切分短语
    chunks = re.split(r"[，。；、,.;:：\n!?！？()（）/\\\-—]+", ev)
    for chunk in chunks:
        if len(chunk) >= min_chunk:
            for src in sources:
                if chunk in src:
                    return True

    # 3. 滑动窗口：任一连续子串命中
    if len(ev) >= min_chunk:
        for start in range(len(ev) - min_chunk + 1):
            for length in range(len(ev) - start, min_chunk - 1, -1):
                sub = ev[start:start + length]
                for src in sources:
                    if sub in src:
                        return True

    # 4. 字符重叠率兜底
    if len(ev) >= 3:
        for src in sources:
            matched = sum(1 for ch in ev if ch in src)
            if matched / len(ev) >= overlap_threshold:
                return True

    return False


def _flatten_five_dim(five_dim: dict) -> list[str]:
    """把六维 dict 中的所有条目展平为字符串列表（供证据匹配与归属校验使用）。"""
    items: list[str] = []
    for value in five_dim.values():
        if isinstance(value, str) and value.strip():
            items.append(value)
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str) and v.strip():
                    items.append(v)
    return items


def _item_in_five_dim(item: str, five_dim_items: list[str]) -> bool:
    """判断要点是否属于六维条目（模糊判断）。

    匹配方式（由严到宽）：
    1. 六维条目是要点的子串，或要点是六维条目的子串
    2. 要点的任一 >= 4 字符连续子串在六维条目中（容忍概括，如"电子信息相关专业"）
    """
    norm_item = _normalize(item)
    if not norm_item:
        return False
    for dim_item in five_dim_items:
        norm_dim = _normalize(dim_item)
        if not norm_dim:
            continue
        if norm_dim in norm_item or norm_item in norm_dim:
            return True
        if len(norm_item) >= 4:
            for i in range(len(norm_item) - 3):
                if norm_item[i:i + 4] in norm_dim:
                    return True
    return False


def _check_dimension(
    dim_key: str,
    dim_data: dict,
    resume_raw: str,
    jd_raw: str,
    resume_extra: list[str],
    jd_extra: list[str],
    errors: list,
) -> None:
    """校验单个维度的分析条目。"""
    dim_id = f"dimensions[{dim_key}]"

    if not isinstance(dim_data, dict):
        errors.append(f"{dim_id} 不是对象")
        return

    gap_level = dim_data.get("gap_level")
    if gap_level not in VALID_GAP_LEVELS:
        errors.append(f"{dim_id} gap_level 非法: {gap_level}")

    summary = str(dim_data.get("summary", ""))
    if not summary.strip():
        errors.append(f"{dim_id} 缺少 summary")
    else:
        # summary 必须能从简历或 JD 原文找到依据（宽松校验）
        if not _evidence_matches(summary, resume_raw + "\n" + jd_raw, resume_extra + jd_extra, min_chunk=4, overlap_threshold=0.5):
            errors.append(f"{dim_id} summary 疑似幻觉（原文中找不到依据）: {summary[:50]}")

    for field in ("missing", "satisfied"):
        items = dim_data.get(field, [])
        if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
            errors.append(f"{dim_id}.{field} 应为字符串数组")
            continue
        if len(items) > 5:
            errors.append(f"{dim_id}.{field} 超过 5 条上限: {len(items)}")
        for item in items:
            # 要点必须来自 JD 六维条目（可信来源）
            if not _item_in_five_dim(item, jd_extra):
                errors.append(f"{dim_id}.{field} 要点不在 JD 六维要求中: {item[:30]}")


def verify_output(state: AgentState) -> dict:
    """校验 analysis_result：维度结构、证据引用、match 一致性。

    Returns:
        包含 verify_result（passed + errors）的字典；
        passed=False 时同时生成 retry_feedback 供下一轮注入 prompt。
    """
    analysis = state.get("analysis_result", {})
    dimensions = analysis.get("dimensions", {})
    extra_gaps = analysis.get("extra_gaps", [])
    resume_raw = (state.get("resume_data") or {}).get("raw_text", "")
    jd_raw = (state.get("jd_data") or {}).get("raw_text", "")
    resume_extra = _flatten_five_dim((state.get("resume_data") or {}).get("five_dim", {}))
    jd_extra = _flatten_five_dim((state.get("jd_data") or {}).get("five_dim", {}))

    errors: list[str] = []

    # ---- dimensions 校验：六维必须齐全 ----
    if not isinstance(dimensions, dict):
        errors.append("dimensions 缺失（应为对象）")
        dimensions = {}

    for dim_key in DIMENSION_KEYS:
        if dim_key not in dimensions:
            errors.append(f"dimensions 缺少维度: {dim_key}")
        else:
            _check_dimension(dim_key, dimensions[dim_key], resume_raw, jd_raw, resume_extra, jd_extra, errors)

    # ---- extra_gaps 校验 ----
    if len(extra_gaps) > EXTRA_GAPS_LIMIT:
        errors.append(f"extra_gaps 超过 {EXTRA_GAPS_LIMIT} 条上限: {len(extra_gaps)}")

    for idx, extra in enumerate(extra_gaps):
        if not isinstance(extra, dict) or not str(extra.get("item", "")).strip():
            errors.append(f"extra_gaps[{idx}] 缺少 item")
            continue
        importance = extra.get("importance")
        if importance not in VALID_IMPORTANCE:
            errors.append(f"extra_gaps[{idx}] importance 非法: {importance}")
        if not _evidence_matches(str(extra["item"]), jd_raw, jd_extra):
            errors.append(f"extra_gaps[{idx}] item 疑似幻觉（JD 原文中找不到依据）: {extra['item'][:40]}")

    # ---- learning_path 校验 ----
    learning_path = analysis.get("learning_path", [])
    if not learning_path:
        errors.append("learning_path 为空（存在 high/medium 差距时应生成学习路径）")
    else:
        if len(learning_path) > 10:
            errors.append(f"learning_path 超过 10 步上限: {len(learning_path)}")
        for i, step in enumerate(learning_path):
            if not isinstance(step, dict) or not step.get("skill"):
                errors.append(f"learning_path[{i}] 缺少 skill 字段")
            importance = step.get("importance")
            if importance not in VALID_IMPORTANCE:
                errors.append(f"learning_path[{i}] importance 非法: {importance}")

    # ---- match 校验 ----
    match = analysis.get("match", {})
    verdict = None
    if not isinstance(match, dict) or not match:
        errors.append("match 总结缺失")
    else:
        verdict = match.get("verdict")
        if verdict not in ("yes", "no"):
            errors.append(f"match.verdict 非法: {verdict}（应为 yes 或 no）")
        if not match.get("reason"):
            errors.append("match.reason 缺失（需要一句话匹配结论）")

    # ---- verdict 一致性校验 ----
    if verdict in ("yes", "no"):
        has_hard_missing = any(
            isinstance(dimensions.get(d), dict)
            and dimensions[d].get("gap_level") == "missing"
            for d in HARD_MISS_DIMENSIONS
        )
        if has_hard_missing and verdict != "no":
            errors.append("verdict 不一致：knowledge/skill/qualifications 存在 missing，verdict 应为 no")
        if not has_hard_missing and verdict != "yes":
            errors.append("verdict 不一致：无硬性维度 missing，verdict 应为 yes")

    passed = not errors

    if passed:
        return {
            "verify_result": {"passed": True, "errors": [], "checked_dims": len(DIMENSION_KEYS)},
        }

    feedback = "\n".join(f"- {e}" for e in errors)
    return {
        "verify_result": {
            "passed": False,
            "errors": errors,
            "checked_dims": len(DIMENSION_KEYS),
        },
        "retry_feedback": (
            "上一轮输出存在以下问题，请严格修正后重新输出完整 JSON：\n" + feedback
        ),
    }
