"""文档加载节点 —— 读取 PDF / Word 简历，输出 Markdown 或纯文本。

策略：markitdown 主通道（保留文档结构），失败时回退 pdfplumber/docx2txt。
"""

from pathlib import Path
from src.state import AgentState


def load_document(state: AgentState) -> dict:
    """根据文件扩展名选择加载器，提取文本后写入 state。

    Returns:
        包含 raw_text、file_type、is_markdown 的字典，或含 error 字段。
    """
    file_path = state.get("file_path", "")

    # ---- 文件存在性检查 ----
    if not file_path:
        return {"error": "未提供 file_path", "file_type": "unknown", "is_markdown": False}

    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}", "file_type": "unknown", "is_markdown": False}

    suffix = path.suffix.lower()

    if suffix not in (".pdf", ".docx", ".doc"):
        return {
            "error": f"不支持的文件类型: {suffix}，仅支持 .pdf / .docx",
            "file_type": "unknown",
            "is_markdown": False,
        }

    # ---- 主通道: markitdown（统一处理 PDF 和 DOCX）----
    try:
        md_text = _load_via_markitdown(str(path))
        if md_text:
            return {"raw_text": md_text, "file_type": suffix.lstrip("."), "is_markdown": True}
    except Exception:
        pass  # markitdown 失败，走兜底

    # ---- 兜底通道: pdfplumber / docx2txt ----
    try:
        if suffix == ".pdf":
            return _load_pdf_fallback(str(path))
        else:
            return _load_docx_fallback(str(path))
    except Exception as exc:
        return {
            "error": f"文档加载失败（主通道和兜底均失败）: {exc}",
            "file_type": suffix.lstrip("."),
            "is_markdown": False,
        }


# ==================== 主通道: markitdown ====================

def _load_via_markitdown(file_path: str) -> str:
    """使用 markitdown 将文档转为 Markdown。"""
    from markitdown import MarkItDown
    md = MarkItDown()
    result = md.convert(file_path)
    text = result.text_content.strip()
    if not text:
        raise ValueError("markitdown 返回空内容")
    return text


# ==================== 兜底通道 ====================

def _load_pdf_fallback(file_path: str) -> dict:
    """pdfplumber 提取 PDF 纯文本。"""
    import pdfplumber
    texts: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
    raw = "\n\n".join(texts).strip()
    if not raw:
        return {"error": "PDF 解析结果为空（可能为扫描件或图片型 PDF）", "file_type": "pdf", "is_markdown": False}
    return {"raw_text": raw, "file_type": "pdf", "is_markdown": False}


def _load_docx_fallback(file_path: str) -> dict:
    """docx2txt 提取 Word 纯文本。"""
    from langchain_community.document_loaders import Docx2txtLoader
    loader = Docx2txtLoader(file_path)
    docs = loader.load()
    raw = "\n\n".join(d.page_content for d in docs).strip()
    if not raw:
        return {"error": "Word 文档解析结果为空", "file_type": "docx", "is_markdown": False}
    return {"raw_text": raw, "file_type": "docx", "is_markdown": False}
