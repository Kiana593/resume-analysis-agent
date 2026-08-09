"""简历提取分析 Web 前端 —— Streamlit 简易界面。

流程：拖入简历 → 提取预览 → Role 排名 → 六维详情 → LLM 差距分析。
运行: streamlit run app.py
"""

import streamlit as st
import sys
import json
import os
import tempfile
from pathlib import Path

# 项目根目录加入 path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.graph import extract_graph, analysis_graph
from src.retrieval.role_loader import load_roles_from_neo4j, rank_roles
from src.main import load_extraction_schema
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
import numpy as np

# ==================== 页面配置 ====================
st.set_page_config(page_title="简历提取分析", page_icon="📄", layout="wide")
st.title("📄 简历提取分析系统")

# ==================== 缓存 ====================

@st.cache_data(ttl=3600)
def cached_load_roles():
    return load_roles_from_neo4j()

@st.cache_data
def run_extract_graph(file_path, mode):
    """运行提取图，返回 resume_data。"""
    schema = load_extraction_schema()
    initial = {
        "file_path": file_path,
        "extraction_schema": schema,
        "user_requirements": None,
        "extraction_mode": mode,
    }
    merged = dict(initial)
    for step_output in extract_graph.stream(initial):
        for _, partial in step_output.items():
            if partial:
                merged.update(partial)
    if merged.get("error"):
        raise RuntimeError(merged["error"])
    return merged.get("resume_data", {})

def run_analysis_graph(resume_data, jd_data):
    """运行分析图（差距分析 + 学习路径）。"""
    initial = {"resume_data": resume_data, "jd_data": jd_data}
    merged = dict(initial)
    for step_output in analysis_graph.stream(initial):
        for node_name, partial in step_output.items():
            if partial:
                merged.update(partial)
    if merged.get("error"):
        raise RuntimeError(merged["error"])
    return merged.get("analysis_result", {})

# ==================== Session State ====================
for key in ["resume_data", "ranked_roles", "selected_role_idx", "analysis_result", "extracted_file", "all_roles"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ==================== Sidebar ====================
with st.sidebar:
    st.header("⚙️ 设置")
    extraction_mode = st.radio(
        "提取模式",
        ["llm", "rule"],
        format_func=lambda x: "🤖 LLM 提取" if x == "llm" else "📐 规则提取",
    )
    if extraction_mode == "llm":
        use_fast = st.checkbox("快速模型 (deepseek-v4-flash)")
        os.environ["DEEPSEEK_MODEL"] = "deepseek-v4-flash" if use_fast else os.environ.pop("DEEPSEEK_MODEL", "deepseek-v4-pro")
    
    st.divider()
    neo4j_ok = bool(os.getenv("NEO4J_PASSWORD"))
    st.caption("Neo4j:" + (" ✅" if neo4j_ok else " ❌ 未配置 .env"))
    
    st.divider()
    st.caption("用法: streamlit run app.py")

# ==================== Step 1: 上传 & 提取 ====================

st.header("1️⃣ 上传简历")
uploaded_file = st.file_uploader(
    "拖拽简历文件到此处（支持 PDF / DOCX）",
    type=["pdf", "docx"],
)

if uploaded_file is not None:
    # 保存到临时文件
    suffix = Path(uploaded_file.name).suffix
    tmp_path = str(PROJECT_ROOT / "results" / f"_upload_{uploaded_file.name}")
    Path(tmp_path).parent.mkdir(exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(uploaded_file.getvalue())
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.info(f"📎 {uploaded_file.name} ({uploaded_file.size / 1024:.0f} KB)")
    with col_b:
        extract_btn = st.button("🔍 提取简历六维", type="primary")
    
    if extract_btn:
        try:
            with st.spinner("正在提取..."):
                rd = run_extract_graph(tmp_path, extraction_mode)
                st.session_state.resume_data = rd
                st.session_state.ranked_roles = None
                st.session_state.selected_role_idx = None
                st.session_state.analysis_result = None
                st.session_state.extracted_file = uploaded_file.name
            st.success("✅ 提取完成！")
            st.rerun()
        except Exception as e:
            st.error(f"提取失败: {e}")

# ==================== Step 2: 简历预览 ====================

if st.session_state.resume_data:
    rd = st.session_state.resume_data
    five_dim = rd.get("five_dim", {})
    
    st.header(f"2️⃣ 简历预览 — {st.session_state.extracted_file or ''}")
    
    # 基本信息行
    pi = five_dim.get("personal_info", {})
    pinfo_items = [f"{k}: {v}" for k, v in pi.items() if v]
    if pinfo_items:
        st.write(" | ".join(pinfo_items))
    
    # 六维展示
    tabs = st.tabs(["📚 知识", "🔧 技能", "🎓 学历", "💪 动机", "🧠 特质", "🤝 自我概念"])
    dim_map = [
        ("knowledge", "知识", five_dim.get("knowledge", [])),
        ("skill", "技能", five_dim.get("skill", [])),
        ("qualifications", "学历/专业", five_dim.get("qualifications", [])),
        ("motivation", "动机", five_dim.get("motivation", [])),
        ("trait", "特质", five_dim.get("trait", [])),
        ("self_concept", "自我概念", five_dim.get("self_concept", [])),
    ]
    for tab, (_, _, items) in zip(tabs, dim_map):
        with tab:
            if items:
                for item in items:
                    st.write(f"• {item}")
            else:
                st.caption("（未提取到）")

# ==================== Step 3: Role 排名 ====================

if st.session_state.resume_data:
    st.header("3️⃣ 职位匹配排名")
    
    if st.button("📊 分析职位匹配", type="primary", disabled=not neo4j_ok):
        try:
            with st.spinner("正在从 Neo4j 加载数据并计算匹配度..."):
                candidate = st.session_state.resume_data.get("five_dim", {})
                roles = cached_load_roles()
                ranked = rank_roles(candidate, roles, topk=20)
                st.session_state.all_roles = roles
                st.session_state.ranked_roles = ranked
                st.session_state.selected_role_idx = None
                st.session_state.analysis_result = None
            st.success(f"共匹配 {len(roles)} 个职业方向，显示 Top {len(ranked)}")
            st.rerun()
        except Exception as e:
            st.error(f"匹配失败: {e}")

# ==================== Step 4: 排名表 + 选择 ====================

if st.session_state.ranked_roles:
    ranked = st.session_state.ranked_roles
    
    # 构建选项
    options = [
        f"#{i} {r['role_name']} — 得分: {r['score']:.4f} — 命中: {r.get('hit_skills',0)}/{r.get('total_skills',0)}"
        for i, r in enumerate(ranked, 1)
    ]
    
    col_sel, col_btn = st.columns([3, 1])
    with col_sel:
        selected_label = st.selectbox(
            "选择职业查看详情",
            options,
            index=st.session_state.selected_role_idx or 0,
            key="role_selector",
        )
        st.session_state.selected_role_idx = options.index(selected_label)
    
    role = ranked[st.session_state.selected_role_idx]
    
    # 雷达图 + 信息
    st.markdown(f"### {role['role_name']}")
    
    col_chart, col_info = st.columns([1, 1])
    
    with col_chart:
        dims = ["知识", "技术", "任职", "动机", "特质", "自我"]
        coverage = role.get("hit_skills", 0) / max(role.get("total_skills", 1), 1)
        values = [coverage] * 6

        angles = np.linspace(0, 2 * np.pi, len(dims), endpoint=False).tolist()
        values += values[:1]
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(4, 4), subplot_kw=dict(polar=True))
        ax.fill(angles, values, alpha=0.25, color='#1f77b4')
        ax.plot(angles, values, color='#1f77b4', linewidth=2)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(dims, fontsize=10)
        ax.set_ylim(0, 1)
        ax.set_yticks([0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(['25%', '50%', '75%', '100%'], fontsize=7)
        ax.set_title(role['role_name'], fontsize=11, pad=15)
        st.pyplot(fig)
    
    with col_info:
        st.metric("匹配得分", f"{role['score']:.4f}")
        st.metric("命中技能", f"{role.get('hit_skills', 0)}/{role.get('total_skills', 0)}")
        st.caption(f"家族: {role.get('family_name', '?')}")
        st.caption(f"领域: {role.get('domain_name', '?')}")
        st.caption(f"旗下 JD 数: {role.get('jd_count', 0)}")
    
    st.divider()
    
    # LLM 差距分析
    st.subheader("🤖 LLM 差距分析")
    
    if st.button("生成匹配分析和学习路径", type="primary"):
        try:
            with st.spinner(f"正在对比 {role['role_name']} 的全部技能要求..."):
                # 从 all_roles 找到该 Role 的完整技能数据
                role_full = None
                for r in st.session_state.get("all_roles", []):
                    if r["role_name"] == role["role_name"]:
                        role_full = r
                        break
                
                if not role_full:
                    st.error("找不到该 Role 的技能数据")
                else:
                    # 用 Role 全部技能构建合成 JD
                    from src.retrieval.scoring import CATEGORY_TO_DIM
                    skills = role_full.get("skills", [])
                    five_dim = {
                        "knowledge": [], "skill": [], "qualifications": [],
                        "motivation": [], "trait": [], "self_concept": [],
                    }
                    for sk in skills:
                        dim = CATEGORY_TO_DIM.get(sk.get("category", ""))
                        name = sk.get("name", "").strip()
                        if dim and name and name not in five_dim[dim]:
                            five_dim[dim].append(name)
                    
                    skill_lines = []
                    for sk in skills[:30]:
                        cat = sk.get("category", "?")
                        name = sk.get("name", "")
                        skill_lines.append(f"[{cat}] {name}")
                    
                    synthetic_jd = {
                        "job_title": role["role_name"],
                        "five_dim": five_dim,
                        "raw_text": f"标准职业: {role['role_name']}\n家族: {role.get('family_name', '')}\n领域: {role.get('domain_name', '')}\n\n核心技能要求:\n" + "\n".join(skill_lines),
                    }
                    
                    st.info(f"对比 {role['role_name']} — {len(skills)} 项核心技能（六维覆盖: { {k: len(v) for k, v in five_dim.items() if v} }）")
                    result = run_analysis_graph(st.session_state.resume_data, synthetic_jd)
                    st.session_state.analysis_result = result
            st.rerun()
        except Exception as e:
            st.error(f"LLM 分析失败: {e}")

# ==================== Step 5: LLM 分析结果 ====================

if st.session_state.analysis_result:
    result = st.session_state.analysis_result
    
    st.header("📋 差距分析结果")
    
    match = result.get("match", {})
    verdict = match.get("verdict", "")
    verdict_icon = "✅" if verdict == "yes" else "❌"
    st.markdown(f"### {verdict_icon} 结论: {'匹配' if verdict == 'yes' else '不匹配'}")
    if match.get("reason"):
        st.info(match["reason"])
    if result.get("overall_summary"):
        st.write(result["overall_summary"])
    
    # 六维差距
    dimensions = result.get("dimensions", {})
    dim_labels = {"knowledge": "知识", "skill": "技术", "qualifications": "任职条件",
                  "motivation": "动机", "trait": "特质", "self_concept": "自我概念"}
    gap_icons = {"missing": "🔴", "partial": "🟡", "sufficient": "🟢"}
    
    for dim in ["knowledge", "skill", "qualifications", "motivation", "trait", "self_concept"]:
        d = dimensions.get(dim, {})
        if not d:
            continue
        gap = d.get("gap_level", "?")
        icon = gap_icons.get(gap, "⚪")
        with st.expander(f"{icon} {dim_labels[dim]} — {gap}", expanded=(gap != "sufficient")):
            st.caption(d.get("summary", ""))
            for key, label in [("missing", "缺失/不足"), ("satisfied", "已满足")]:
                items = d.get(key, [])
                if items:
                    st.write(f"**{label}:**")
                    for item in items:
                        st.write(f"• {item}")
    
    # 学习路径
    learning_path = result.get("learning_path", [])
    if learning_path:
        st.subheader("📚 学习路径")
        for step in learning_path:
            imp = step.get("importance", "?")
            icon = {"high": "🔴", "medium": "🟡"}.get(imp, "⚪")
            st.write(f"{icon} **{step.get('step', '?')}.** {step.get('skill', '?')} `[{imp}]`")
    
    if st.button("🔄 重新分析"):
        st.session_state.analysis_result = None
        st.rerun()

st.divider()
st.caption("简历提取分析 Agent | Neo4j + DeepSeek | LangGraph + Streamlit")
