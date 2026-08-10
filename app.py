"""简历提取分析 Web 前端（轻量化版）。

流程：上传简历 → markitdown 转 Markdown → 原文命中搜索 → Role 排名 → 雷达图 + 高亮 → LLM 建议。
运行: streamlit run app.py
"""

import streamlit as st
import sys, os, json, tempfile, re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.retrieval.role_loader import load_roles_from_neo4j, rank_roles, compute_dimension_hits, DIM_LABELS
from src.prompts.gap_analysis import ROLE_GAP_PROMPT
from src.utils.llm import call_deepseek_json

import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
import numpy as np

# ==================== 页面配置 ====================
st.set_page_config(page_title="简历分析", page_icon="📄", layout="wide")
st.title("📄 简历职位匹配分析")

DIM_ORDER = ("knowledge", "skill", "qualifications", "preference", "motivation", "trait", "self_concept")

# ==================== 缓存 ====================
@st.cache_data(ttl=3600)
def cached_load_roles():
    return load_roles_from_neo4j()

def convert_to_markdown(file_bytes, suffix):
    """PDF/DOCX -> Markdown via markitdown."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(tmp_path)
        text = result.text_content.strip()
        if not text:
            raise ValueError("markitdown returned empty")
        return text
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

def highlight_text(raw_text, hits):
    """在原文中高亮命中的技能名。"""
    # Collect all positions, sort by start
    positions = []
    for h in hits:
        for s, e in h.get("positions", []):
            positions.append((s, e))
    positions.sort(key=lambda x: x[0])

    # Merge overlapping
    merged = []
    for s, e in positions:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Build highlighted text
    result = []
    prev = 0
    for s, e in merged:
        # Escape HTML
        seg = raw_text[prev:s].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        hit_seg = raw_text[s:e].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        result.append(seg)
        result.append(f"<mark>{hit_seg}</mark>")
        prev = e
    result.append(raw_text[prev:].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(result)

# ==================== Session State ====================
for key in ["raw_text", "ranked_roles", "selected_role_idx", "analysis_result", "all_roles", "uploaded_name"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ==================== Sidebar ====================
with st.sidebar:
    st.header("⚙️ 设置")
    use_fast = st.checkbox("快速模型 (deepseek-v4-flash)")
    os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-flash" if use_fast else os.environ.pop("DEEPSEEK_MODEL", "deepseek-v4-pro")

    st.divider()
    neo4j_ok = bool(os.getenv("NEO4J_PASSWORD"))
    st.caption(f"Neo4j: {'✅' if neo4j_ok else '❌ 未配置'}")

# ==================== Step 1: Upload ====================
st.header("1️⃣ 上传简历")
uploaded_file = st.file_uploader("拖拽 PDF/DOCX 到此处", type=["pdf", "docx"])

if uploaded_file is not None:
    suffix = Path(uploaded_file.name).suffix
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.info(f"📎 {uploaded_file.name} ({uploaded_file.size / 1024:.0f} KB)")
    with col_b:
        if st.button("📄 解析简历", type="primary"):
            with st.spinner("markitdown 转换中..."):
                try:
                    raw = convert_to_markdown(uploaded_file.getvalue(), suffix)
                    st.session_state.raw_text = raw
                    st.session_state.ranked_roles = None
                    st.session_state.selected_role_idx = None
                    st.session_state.analysis_result = None
                    st.session_state.uploaded_name = uploaded_file.name
                    st.success(f"✅ 解析完成 ({len(raw)} 字符)")
                    st.rerun()
                except Exception as e:
                    st.error(f"解析失败: {e}")

# ==================== Step 2: Markdown Preview ====================
if st.session_state.raw_text:
    st.header(f"2️⃣ 简历原文 — {st.session_state.uploaded_name or ''}")
    with st.expander("查看 Markdown 原文", expanded=False):
        st.text_area("", st.session_state.raw_text, height=300, label_visibility="collapsed")

# ==================== Step 3: Rank ====================
if st.session_state.raw_text:
    st.header("3️⃣ 职位匹配排名")

    if st.button("📊 开始匹配", type="primary", disabled=not neo4j_ok):
        with st.spinner("匹配中..."):
            try:
                roles = cached_load_roles()
                ranked = rank_roles(st.session_state.raw_text, roles, topk=20)
                st.session_state.all_roles = roles
                st.session_state.ranked_roles = ranked
                st.session_state.selected_role_idx = None
                st.session_state.analysis_result = None
                st.success(f"共 {len(roles)} 个职业，显示 Top {len(ranked)}")
                st.rerun()
            except Exception as e:
                st.error(f"匹配失败: {e}")

# ==================== Step 4: Role Detail ====================
if st.session_state.ranked_roles:
    ranked = st.session_state.ranked_roles
    options = [
        f"#{i} {r['role_name']} — {r['score']:.2%} — {r.get('hit_skills',0)}/{r.get('total_skills',0)}"
        for i, r in enumerate(ranked, 1)
    ]

    selected_label = st.selectbox(
        "选择职业查看详情", options,
        index=st.session_state.selected_role_idx or 0,
    )
    st.session_state.selected_role_idx = options.index(selected_label)
    role = ranked[st.session_state.selected_role_idx]

    # Find full role data
    role_full = None
    for r in st.session_state.get("all_roles", []):
        if r["role_name"] == role["role_name"]:
            role_full = r
            break

    st.markdown(f"### {role['role_name']}")

    col_chart, col_info = st.columns([1, 1])

    # Radar chart
    if role_full:
        dim_hits = compute_dimension_hits(st.session_state.raw_text, role_full.get("skills", []))
    else:
        dim_hits = {}

    with col_chart:
        dim_labels_chart = []
        values = []
        for dim in DIM_ORDER:
            d = dim_hits.get(dim)
            dim_labels_chart.append(DIM_LABELS.get(dim, dim))
            values.append(d["coverage"] if d else 0.0)

        angles = np.linspace(0, 2 * np.pi, len(DIM_ORDER), endpoint=False).tolist()
        vals_plot = values + values[:1]
        angles_plot = angles + angles[:1]

        fig, ax = plt.subplots(figsize=(4, 4), subplot_kw=dict(polar=True))
        ax.fill(angles_plot, vals_plot, alpha=0.25, color="#1f77b4")
        ax.plot(angles_plot, vals_plot, color="#1f77b4", linewidth=2)
        ax.set_xticks(angles)
        ax.set_xticklabels(dim_labels_chart, fontsize=9)
        ax.set_ylim(0, 1)
        ax.set_yticks([0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(["25%", "50%", "75%", "100%"], fontsize=7)
        ax.set_title(role["role_name"], fontsize=11, pad=15)
        st.pyplot(fig)

    with col_info:
        st.metric("匹配得分", f"{role['score']:.2%}")
        st.metric("命中技能", f"{role.get('hit_skills', 0)}/{role.get('total_skills', 0)}")

    # Hit/miss details
    if dim_hits:
        st.subheader("技能命中明细")
        cols = st.columns(4)
        for i, dim in enumerate(DIM_ORDER):
            d = dim_hits.get(dim)
            if not d:
                continue
            with cols[i % 4]:
                label = DIM_LABELS.get(dim, dim)
                cov = d["coverage"]
                color = "green" if cov >= 0.8 else ("orange" if cov >= 0.4 else "red")
                st.markdown(f"**{label}** :{color}[{d['hit_count']}/{d['total']} = {cov:.0%}]")
                if d["miss"]:
                    with st.expander(f"未命中 ({len(d['miss'])})"):
                        for m in d["miss"]:
                            st.write(f"❌ {m}")
                if d["hit"]:
                    with st.expander(f"已命中 ({len(d['hit'])})"):
                        for h in d["hit"]:
                            st.write(f"✅ {h}")

    # Highlighted markdown
    if role_full:
        full_result = None
        for r in st.session_state.get("all_roles", []):
            if r["role_name"] == role["role_name"]:
                from src.retrieval.scoring import match_skills_in_text
                full_result = match_skills_in_text(st.session_state.raw_text, r.get("skills", []))
                break

        if full_result:
            st.subheader("🔍 简历原文匹配高亮")
            highlighted = highlight_text(st.session_state.raw_text, full_result["hit"])
            # Truncate for display
            max_display = 5000
            display_text = highlighted[:max_display]
            if len(highlighted) > max_display:
                display_text += "\n\n... (共 " + str(len(st.session_state.raw_text)) + " 字符，仅显示前 " + str(max_display) + ")"
            st.markdown(display_text, unsafe_allow_html=True)

    st.divider()

    # ==================== Step 5: LLM Analysis ====================
    st.subheader("🤖 LLM 分析")

    if st.button("生成匹配分析和学习路径", type="primary"):
        try:
            with st.spinner("LLM 分析中..."):
                # Build dimension details for prompt
                parts = []
                for dim in DIM_ORDER:
                    d = dim_hits.get(dim)
                    if not d:
                        continue
                    label = DIM_LABELS.get(dim, dim)
                    hit_str = ", ".join(d["hit"][:8]) if d["hit"] else "(none)"
                    miss_str = ", ".join(d["miss"][:8]) if d["miss"] else "(none)"
                    parts.append(
                        f"**{label}** ({d['hit_count']}/{d['total']}):\n"
                        f"  Hit: {hit_str}\n"
                        f"  Miss: {miss_str}"
                    )

                prompt = ROLE_GAP_PROMPT.format(
                    role_name=role_full.get("role_name", ""),
                    family_name=role_full.get("family_name", ""),
                    domain_name=role_full.get("domain_name", ""),
                    dimension_details="\n\n".join(parts),
                    resume_raw_text=st.session_state.raw_text[:1500],
                )
                result = call_deepseek_json(prompt)
                st.session_state.analysis_result = result
                st.success("分析完成")
            st.rerun()
        except Exception as e:
            st.error(f"LLM 分析失败: {e}")

# ==================== Step 6: LLM Result ====================
if st.session_state.analysis_result:
    result = st.session_state.analysis_result
    st.header("📋 分析结果")

    match = result.get("match", {})
    verdict = match.get("verdict", "")
    icon = "✅" if verdict == "yes" else "❌"
    st.markdown(f"### {icon} 结论: {'匹配' if verdict == 'yes' else '不匹配'}")
    if match.get("reason"):
        st.info(match["reason"])
    if result.get("overall_summary"):
        st.write(result["overall_summary"])

    dimensions = result.get("dimensions", {})
    gap_icons = {"missing": "red", "partial": "orange", "sufficient": "green"}
    for dim in DIM_ORDER:
        d = dimensions.get(dim, {})
        if not d:
            continue
        gap = d.get("gap_level", "?")
        label = DIM_LABELS.get(dim, dim)
        with st.expander(f":{gap_icons.get(gap, 'gray')}[{label} — {gap}]", expanded=(gap != "sufficient")):
            st.caption(d.get("summary", ""))

    learning_path = result.get("learning_path", [])
    if learning_path:
        st.subheader("📚 学习路径")
        for step in learning_path:
            imp = step.get("importance", "?")
            icon = {"high": "🔴", "medium": "🟡"}.get(imp, "⚪")
            st.write(f"{icon} **{step.get('step', '?')}.** {step.get('skill', '?')} [{imp}]")

st.divider()
st.caption("简历职位匹配分析 | Neo4j + DeepSeek + Streamlit")
