# -*- coding: utf-8 -*-
"""取 FairCV 简历的 content 走 LLM 提取（等价 extract 流程，跳过文档加载）。

用法:
    python tools/run_faircv_extract.py [索引]
"""
import json, io, sys, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os = __import__("os")
os.chdir(str(ROOT))
from dotenv import load_dotenv
load_dotenv()

from src.nodes.llm_extractor import llm_extract
from src.state import AgentState
from src.main import load_extraction_schema

SAMPLE = ROOT / "samples" / "faircv_sample_100.json"
IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 0

with io.open(SAMPLE, encoding="utf-8") as f:
    resumes = json.load(f)
resume = resumes[IDX]
meta = resume["metadata"]
content = resume["content"]
print(f"处理: #{IDX} {meta['position']} (skill_level={meta['skill_level']})")
print(f"内容长度: {len(content)} 字符")

schema = load_extraction_schema()
state = AgentState({
    "file_path": f"faircv_{IDX:03d}_{meta['position']}.txt",
    "raw_text": content,
    "is_markdown": True,
    "extraction_schema": schema,
    "user_requirements": None,
})

result = llm_extract(state)
if "error" in result:
    print("[ERROR]", result["error"])
    sys.exit(1)

five_dim = result["extraction_result"]
print("\n=== LLM 提取结果 ===")
print(json.dumps(five_dim, ensure_ascii=False, indent=2))

# 保存（与 save_resume 一致格式）
results_dir = os.path.join(os.getcwd(), "results")
safe = f"faircv_{IDX:03d}_{meta['position']}"
safe = re.sub(r"[^\w\-]", "_", safe)
ts = time.strftime("%Y%m%d_%H%M%S")
out_path = os.path.join(results_dir, f"{safe}_{ts}.json")
resume_data = {
    "file_name": f"{safe}.txt",
    "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "raw_text": content,
    "is_markdown": True,
    "five_dim": five_dim,
}
with io.open(out_path, "w", encoding="utf-8") as f:
    json.dump(resume_data, f, ensure_ascii=False, indent=2)
print("\n已保存:", out_path)