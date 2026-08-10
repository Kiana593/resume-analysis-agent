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
from src.retrieval.scoring import match_skills_in_text
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

    # ===== Global LLM Enhancement =====
    st.divider()
    st.subheader(":mag: LLM 全局增强验证")
    st.caption("收集 Top-20 职业的未命中技能（去重），一次 LLM 调用验证，重排")

    col_e1, col_e2 = st.columns([2, 1])
    with col_e2:
        do_enhance = st.button("🤖 LLM 增强验证 Top-20", type="secondary")

    if do_enhance:
        all_missed = {}
        for role in st.session_state.ranked_roles:
            role_full2 = None
            for rr in st.session_state.get("all_roles", []):
                if rr["role_name"] == role["role_name"]:
                    role_full2 = rr
                    break
            if not role_full2:
                continue
            from src.retrieval.scoring import match_skills_in_text
            result = match_skills_in_text(st.session_state.raw_text, role_full2.get("skills", []))
            for m in result["miss"]:
                name = m["name"]
                if name not in all_missed:
                    all_missed[name] = m

        if not all_missed:
            st.info("没有未命中技能需要验证")
        else:
            miss_list = list(all_missed.keys())
            with st.spinner(f"LLM verifying {len(miss_list)} unique missed skills..."):
                miss_items = "\n".join(f"{i+1}. {n}" for i, n in enumerate(miss_list))
                vlines = []
                vlines.append("You are a resume skill verifier.")
                vlines.append("Check each skill below against the resume.")
                vlines.append("If present (different wording OK, e.g. K8s=Kubernetes), mark true.")
                vlines.append("")
                vlines.append("## Resume")
                vlines.append(st.session_state.raw_text[:4000])
                vlines.append("")
                vlines.append("## Skills to verify")
                vlines.append(miss_items)
                vlines.append("")
                vlines.append("## Output (JSON only, no extra text)")
                vlines.append('{"verified_skills": ["exact skill name 1", "exact skill name 2"]}')
                verify_prompt = "\n".join(vlines)

                try:
                    llm_result = call_deepseek_json(verify_prompt)
                    verified_list = llm_result.get("verified_skills", [])
                    st.session_state.global_verified = set()
                    for name in verified_list:
                        name = name.strip()
                        if name in all_missed:
                            st.session_state.global_verified.add(name)
                    vcnt = len(st.session_state.global_verified)
                    st.success(f"LLM 确认 {vcnt}/{len(miss_list)} 项未命中为实际命中")

                    # Re-rank Top-20
                    enhanced = []
                    for role in st.session_state.ranked_roles:
                        rf = None
                        for rr in st.session_state.get("all_roles", []):
                            if rr["role_name"] == role["role_name"]:
                                rf = rr
                                break
                        if not rf:
                            enhanced.append(role)
                            continue
                        skills = rf.get("skills", [])
                        gv = st.session_state.global_verified
                        extra = sum(1 for sk in skills if sk.get("name","").strip() in gv)
                        total = role.get("total_skills", 1)
                        nh = role.get("hit_skills", 0) + extra
                        ns = round(nh / max(total, 1) * min(1.0, total / 10), 4)
                        nr = dict(role)
                        nr["hit_skills"] = nh
                        nr["score"] = ns
                        nr["_enhanced"] = True
                        nr["_extra_hits"] = extra
                        enhanced.append(nr)
                    enhanced.sort(key=lambda x: x["score"], reverse=True)
                    st.session_state.ranked_roles = enhanced
                    st.session_state.selected_role_idx = 0
                    st.rerun()
                except Exception as e:
                    st.error(f"LLM enhancement failed: {e}")

    if st.session_state.get("global_verified"):
        gv = st.session_state.global_verified
        if gv:
            st.caption(f":white_check_mark: {len(gv)} 项 LLM 确认命中")
            with st.expander("查看已确认技能"):
                for name in sorted(gv):
                    st.write(f":white_check_mark: {name}")

    st.divider()

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

    # Apply LLM verification to dim_hits
    verified_set = st.session_state.get("global_verified", set())
    if verified_set:
        for dim in DIM_ORDER:
            d = dim_hits.get(dim)
            if not d:
                continue
            newly_hit = [m for m in d["miss"] if m in verified_set]
            if newly_hit:
                for v in newly_hit:
                    d["miss"].remove(v)
                    d["hit"].append(v)
                d["hit_count"] = len(d["hit"])
                d["miss_count"] = len(d["miss"])
                d["coverage"] = round(d["hit_count"] / max(d["total"], 1), 4)

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
    st.subheader("技能命中明细")
    cols = st.columns(4)
    for i, dim in enumerate(DIM_ORDER):
        d = dim_hits.get(dim)
        with cols[i % 4]:
            label = DIM_LABELS.get(dim, dim)
            if d:
                cov = d["coverage"]
                color = "green" if cov >= 0.8 else ("orange" if cov >= 0.4 else "red")
                st.markdown(f"**{label}** :{color}[{d['hit_count']}/{d['total']} = {cov:.0%}]")
                if d["miss"]:
                    with st.expander(f"未命中 ({len(d['miss'])})"):
                        for m in d["miss"]:
                            st.write(f"❌ {m}")
                if d["hit"]:
                    with st.expander(f"✅ 已命中 ({len(d['hit'])})"):
                        for h in d["hit"]:
                            tag = "🧠 " if h in verified_set else ""
                            st.write(f"✅ {tag}{h}")
            else:
                st.markdown(f"**{label}** :gray[0/0]")
                st.caption("该职业无此维度技能")


    # Show global LLM enhanced status for this role
    if role.get("_enhanced"):
        st.caption(f":brain: LLM增强: +{role.get("_extra_hits", 0)} 命中, 得分 {role["score"]:.2%}")

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
                                # Build dimension details (dim_hits already includes LLM-verified hits)
                parts = []
                for dim in DIM_ORDER:
                    d = dim_hits.get(dim)
                    if not d:
                        continue
                    label = DIM_LABELS.get(dim, dim)
                    hit_names = list(d["hit"]) if d["hit"] else []
                    miss_names = list(d["miss"]) if d["miss"] else []
                    hit_str = ", ".join(hit_names[:8]) if hit_names else "(none)"
                    miss_str = ", ".join(miss_names[:8]) if miss_names else "(none)"
                    parts.append(
                        f"**{label}** ({d["hit_count"]}/{d["total"]}):\n"
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