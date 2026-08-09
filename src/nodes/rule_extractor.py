"""规则提取节点 —— 从 Markdown 简历文本中自动提取六维信息（非 LLM）。

与 llm_extractor 并行的替代路线，通过正则、关键词匹配和章节解析
从 Markdown 文本中提取六维画像（knowledge / skill / qualifications /
motivation / trait / self_concept + personal_info）。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from src.state import AgentState

# ==================== 章节识别 ====================

SECTION_LABELS = {
    "personal": ["个人信息", "个人资料", "基本信息", "个人简历", "联系信息", "基础信息"],
    "education": ["教育背景", "教育经历", "教育", "学历", "学习经历", "学业背景"],
    "skill": ["专业技能", "技能", "技术栈", "专业能力", "技术能力", "掌握技能", "核心能力", "核心技能", "能力"],
    "project": ["项目经验", "项目经历", "项目", "作品", "产品经验", "科研成果"],
    "work": ["工作经历", "工作经验", "工作", "实习经历", "职业经历", "从业经历"],
    "self_eval": ["自我评价", "个人评价", "自我介绍", "自我描述", "关于我", "个人总结"],
    "cert": ["证书", "资格认证", "资质", "获奖", "荣誉", "奖项"],
}

# ==================== 个人信息正则 ====================

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"1[3-9]\d[-\s.]?\d{4}[-\s.]?\d{4}")
_CITY_SUFFIX = re.compile(r".{2,8}(?:市|省|区|县)$")
_JOB_INTENT_RE = re.compile(r"(?:求职意向|期望职位|意向岗位|应聘岗位|期望岗位)[：:\s]*([^\n]{2,30})")

# ==================== 学历 / 知识 ====================

_DEGREE_MAP = [
    (r"博士", "博士学历"),
    (r"硕士|研究生", "硕士学历"),
    (r"MBA|工商管理硕士", "MBA学历"),
    (r"本科|学士", "本科学历"),
    (r"大专|专科", "大专学历"),
]

_COURSE_BLOCK_RE = re.compile(r"(?:相关课程|专业课程|主要课程|核心课程|课程)[：:\s]*([^\n]*)")
_COURSE_ITEM_RE = re.compile(r"^\s*[-\u2022*]\s*(.{2,40})$", re.M)

_MAJOR_RE = re.compile(
    r"(?:专业[：:]\s*)?"
    r"([\u4e00-\u9fa5A-Za-z]{2,12}"
    r"(?:科学与技术|工程|科学|技术|管理|设计|信息|学|医学|法学|经济|文学)"
    r"\s*(?:专业|方向)?)"
    r"(?![\u4e00-\u9fa5])"
)

# ==================== 技能提取 ====================

_SKILL_HINTS = [
    "熟练", "精通", "掌握", "使用", "开发", "设计", "实现", "搭建",
    "调试", "部署", "编写", "分析", "优化", "构建", "维护", "测试",
    "配置", "集成", "管理", "运用", "撰写", "调研", "规划", "推动",
    "输出", "评审", "协调", "落地", "推进", "迭代", "运营", "梳理",
]
_EXCLUDE_SKILL = re.compile(
    r"项目|成果|时间|GPA|排名|获奖|荣誉|职责|角色|贡献|规模|选型$|"
    r"发表|申请|阅读量|参与|领导|指导|协调|组织|"
    r"^推动|^负责|^协助|^跟进|^参与|^整理|^协调$|^设计$|"
    r"^优化$|^管理$|^实现$|^构建$|^开发$|^分析$|^测试$|"
    r"落地|上线|协同|复盘|联调|评审$|对接|沟通$|"
    r"设计和|推动和"
)
_TECH_TERM_RE = re.compile(r"[A-Za-z+#.]{2,}")

# ==================== 软性维度关键词 ====================

_MOTIVATION_KW = [
    ("热爱", "对技术有热情"), ("热情", "对技术有热情"), ("兴趣", "对技术有热情"),
    ("积极主动", "积极主动"), ("主动", "积极主动"),
    ("学习意愿", "学习意愿"), ("好学", "学习意愿"),
    ("自我驱动", "自我驱动"), ("自驱", "自我驱动"),
    ("上进", "上进心"), ("进取", "上进心"),
    ("探索", "探索精神"), ("求知", "求知欲"),
    ("持续学习", "持续学习"), ("成长", "成长意愿"),
    ("追求", "追求卓越"), ("挑战", "乐于挑战"),
]
_TRAIT_KW = [
    ("沟通能力", "沟通能力"), ("沟通协调", "沟通协调能力"), ("沟通", "沟通能力"),
    ("团队合作", "团队协作"), ("团队协作", "团队协作"), ("协作", "团队协作"),
    ("逻辑思维", "逻辑思维"), ("逻辑", "逻辑思维"),
    ("学习能力", "学习能力"), ("快速学习", "快速学习能力"),
    ("问题解决", "问题解决"), ("解决问题", "问题解决"),
    ("抗压", "抗压能力"), ("抗压能力", "抗压能力"),
    ("细心", "细心"), ("细致", "细致"), ("耐心", "耐心"),
    ("创新思维", "创新思维"), ("创新", "创新思维"),
    ("数据驱动", "数据驱动"), ("数据", "数据分析能力"),
    ("执行力", "执行力"), ("执行", "执行力"),
    ("领导力", "领导力"), ("领导", "领导力"),
    ("责任心", "责任心"), ("责任感", "责任心"),
    ("适应能力", "适应能力"), ("适应", "适应能力"),
    ("文档", "文档能力"), ("表达", "表达能力"),
]
_SELF_CONCEPT_KW = [
    ("责任心", "责任心"), ("责任", "责任心"),
    ("团队合作意识", "团队合作意识"), ("团队意识", "团队合作意识"),
    ("主人翁", "主人翁意识"),
    ("职业素养", "职业素养"), ("职业", "职业素养"),
    ("敬业", "敬业精神"),
    ("担当", "担当意识"),
    ("奉献", "奉献精神"),
    ("合作", "团队合作意识"),
]

# ==================== 章节解析 ====================

def _parse_sections(text):
    lines = text.splitlines()
    sections = {}
    current = "__head__"

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^(?:#{1,4}\s*|\*\*)([^*#\n]{1,30})(?:\*\*)?\s*$", stripped)
        if m:
            title = m.group(1).strip()
            if len(title) <= 25 and not any(c in title for c in "：:@/\\"):
                current = title
                sections.setdefault(current, [])
                continue
        if re.match(r"^[-=]{3,}$", stripped):
            continue
        sections.setdefault(current, []).append(stripped)

    return sections


def _classify_section(title):
    for sec_type, labels in SECTION_LABELS.items():
        for label in labels:
            if label in title:
                return sec_type
    return None


def _collect_by_type(sections, sec_type):
    lines = []
    for title, content in sections.items():
        if _classify_section(title) == sec_type:
            lines.extend(content)
    return lines


# ==================== 个人信息提取 ====================

def _extract_personal_info(text):
    info = {
        "姓名": "", "性别": "", "年龄/出生日期": "",
        "联系电话": "", "邮箱": "", "现居城市": "", "求职意向": "",
    }
    m = re.search(r"(?:姓名|名字)[：:\s]*([^\s\n]{2,4})", text)
    if m:
        info["姓名"] = m.group(1).strip()
    m = re.search(r"性别[：:\s]*(男|女)", text)
    if m:
        info["性别"] = m.group(1)
    m = re.search(r"(?:年龄|出生日期)[：:\s]*([^\s\n]{2,10})", text)
    if m:
        info["年龄/出生日期"] = m.group(1).strip()
    m = _PHONE_RE.search(text)
    if m:
        info["联系电话"] = m.group()
    m = _EMAIL_RE.search(text)
    if m:
        info["邮箱"] = m.group()
    m = re.search(r"(?:现居|所在城市|户口地|所在地|城市)[：:\s]*([^\s\n]{2,8})", text)
    if m:
        city = m.group(1).strip()
        if _CITY_SUFFIX.search(city) or "市" in city or "省" in city:
            info["现居城市"] = city
    m = _JOB_INTENT_RE.search(text)
    if m:
        info["求职意向"] = m.group(1).strip()
    return info


# ==================== 学历 / 知识提取 ====================

def _extract_qualifications_and_knowledge(text, sections):
    edu_lines = _collect_by_type(sections, "education")
    edu_text = "\n".join(edu_lines) if edu_lines else text

    qualifications = []
    for pattern, label in _DEGREE_MAP:
        if re.search(pattern, edu_text) and label not in qualifications:
            qualifications.append(label)

    m = _MAJOR_RE.search(edu_text)
    if m:
        major = re.sub(r"\s+", "", m.group(1))
        if not major.endswith("专业"):
            major += "专业"
        if major not in qualifications:
            qualifications.append(major)

    knowledge = []
    for cm in _COURSE_BLOCK_RE.findall(edu_text):
        for part in re.split(r"[、,，;；]", cm):
            part = part.strip()
            if part and len(part) <= 40 and not re.match(r"^[-*0-9.]+$", part):
                knowledge.append(part)

    for line in edu_lines:
        m = _COURSE_ITEM_RE.match(line)
        if not m:
            continue
        item = m.group(1).strip()
        if (re.search(r"算法|系统|原理|网络|数据库|编程|结构|开发|设计|课程|工程", item)
                and len(item) <= 40 and item not in knowledge):
            knowledge.append(item)

    knowledge = list(dict.fromkeys(knowledge))[:12]
    return qualifications, knowledge


# ==================== 技能提取 ====================

def _extract_skill(text, sections):
    # 收集技能章节和项目章节（分策略处理）
    raw_skill_lines = _collect_by_type(sections, "skill")
    raw_project_lines = _collect_by_type(sections, "project")

    skills = []

    def _clean_line(line):
        s = re.sub(r"^[-\u2022*\d.]+\s*", "", line).strip()
        if not s or len(s) < 2:
            return None
        if re.match(r"^#{1,4}\s", s):
            return None
        if "。" in s:
            return None
        if re.search(r"\uff5c.*\d{4}\.\d|\d{4}\.\d.*\uff5c|\uff5c.*实习|实习.*\uff5c", s):
            return None
        if re.match(r"^\d{4}[.\-/年]\d{1,2}[.\-/月]\d{0,2}", s):
            return None
        return s

    # 技能章节：所有条目都是技能，不需要 hint 过滤
    for line in raw_skill_lines:
        s = _clean_line(line)
        if not s:
            continue
        for part in re.split(r"[、,，;；]", s):
            part = re.sub(r"[（(].*?[)）]", "", part).strip()
            part = re.sub(r"[（(][^)）]*$", "", part).strip()
            part = re.sub(r"^[^：:]{1,12}[：:]\s*", "", part).strip()
            if not part or len(part) < 3 or len(part) > 40:
                continue
            if _EXCLUDE_SKILL.search(part):
                continue
            if part not in skills:
                skills.append(part)

    # 项目章节：需要有技能特征词才收入
    for line in raw_project_lines:
        s = _clean_line(line)
        if not s:
            continue
        for part in re.split(r"[、,，;；]", s):
            part = re.sub(r"[（(].*?[)）]", "", part).strip()
            part = re.sub(r"[（(][^)）]*$", "", part).strip()
            part = re.sub(r"^[^：:]{1,12}[：:]\s*", "", part).strip()
            if not part or len(part) < 3 or len(part) > 40:
                continue
            if _EXCLUDE_SKILL.search(part):
                continue
            has_tech = bool(_TECH_TERM_RE.search(part))
            has_hint = any(h in part for h in _SKILL_HINTS)
            if has_tech or has_hint:
                if part not in skills:
                    skills.append(part)

    return list(dict.fromkeys(skills))[:20]


# ==================== 软性维度提取 ====================

def _match_keywords(text, mapping):
    out = []
    for kw, label in mapping:
        if kw in text and label not in out:
            out.append(label)
    return out


def _extract_soft_dims(text, sections):
    # 收集所有可能含软性描述的章节
    source_lines = []
    for sec_type in ("self_eval", "other", "cert"):
        source_lines.extend(_collect_by_type(sections, sec_type))
    # 也加入文件头部（自我评价可能在无标题区域）
    source_lines.extend(sections.get("__head__", []))
    eval_text = "\n".join(source_lines) if source_lines else text

    # 如果 self_eval 为空，在全文搜索"自我"附近段落
    self_eval_lines = _collect_by_type(sections, "self_eval")
    if not self_eval_lines:
        m = re.search(r"自我[^\n]{0,500}", text)
        if m:
            eval_text = eval_text + "\n" + m.group()

    motivation = _match_keywords(eval_text, _MOTIVATION_KW)
    trait = _match_keywords(eval_text, _TRAIT_KW)
    self_concept = _match_keywords(eval_text, _SELF_CONCEPT_KW)

    # 兜底：从全文简单匹配
    if not motivation:
        for kw in ["热爱", "热情", "主动", "自驱", "上进", "求知", "学习意愿", "探索"]:
            if kw in text:
                motivation.append("积极主动" if kw in ("主动",) else ("对技术有热情" if kw in ("热爱", "热情") else kw))
                break
    if not trait:
        for kw in ["沟通", "协作", "逻辑", "学习", "抗压", "细致", "创新", "数据"]:
            if kw in text:
                trait.append(kw + "能力")
                break
    if not self_concept:
        for kw in ["责任", "团队", "主人翁", "敬业", "担当", "奉献"]:
            if kw in text:
                label = kw + "心" if kw == "责任" else (kw + "意识" if kw in ("团队", "主人翁", "担当") else kw + "精神")
                self_concept.append(label)
                break

    return motivation, trait, self_concept


# ==================== 主入口 ====================

def rule_extract(state):
    """从 raw_text（Markdown）中规则提取六维信息。"""
    raw_text = state.get("raw_text", "").strip()
    if not raw_text:
        return {"error": "raw_text 为空，无法进行规则提取"}

    try:
        sections = _parse_sections(raw_text)
        personal_info = _extract_personal_info(raw_text)
        qualifications, knowledge = _extract_qualifications_and_knowledge(raw_text, sections)
        skill = _extract_skill(raw_text, sections)
        motivation, trait, self_concept = _extract_soft_dims(raw_text, sections)

        result = {
            "personal_info": personal_info,
            "knowledge": knowledge,
            "skill": skill,
            "qualifications": qualifications,
            "motivation": motivation,
            "trait": trait,
            "self_concept": self_concept,
        }
        return {"extraction_result": result}
    except Exception as exc:
        return {"error": "规则提取失败: " + str(exc)}
