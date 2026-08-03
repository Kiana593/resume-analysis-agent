"""入口 —— CLI 驱动：extract / analyze / batch-analyze / rank。

用法示例：
    python src/main.py extract samples/test2.pdf -r "重点关注AI项目经验"
    python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json
    python src/main.py batch-analyze -r results/test2.json -j jds --workers 3
    python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json --fast
    python src/main.py rank -r results/test2.json -j jds --topk 3
"""

import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph import extract_graph, analysis_graph
from src.retrieval.scoring import rank_jds, DIMENSION_KEYS
from src.retrieval.neo4j_loader import load_jds_from_local, load_jds_from_neo4j

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
ANALYSIS_DIR = RESULTS_DIR / "analysis"

RAW_SUFFIXES = {".pdf", ".docx", ".doc"}

FAST_MODEL = "deepseek-v4-flash"


# ==================== 工具函数 ====================

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_extraction_schema() -> dict:
    schema_path = Path(__file__).resolve().parent / "config" / "extraction_schema.json"
    return load_json(str(schema_path))


def _stream(graph, initial_state) -> dict:
    """运行图，合并所有节点返回的状态更新，返回最终完整状态。"""
    merged = dict(initial_state)
    for step_output in graph.stream(initial_state):
        for node_name, partial in step_output.items():
            if not partial:
                continue
            merged.update(partial)
            err = partial.get("error")
            if err:
                print(f"  [{node_name}] 错误: {err}")
            elif node_name == "load_document":
                print(f"  [{node_name}] 完成 (文本长度: {len(partial.get('raw_text', ''))} 字符)")
            elif node_name == "verify_output":
                v = partial.get("verify_result", {})
                if v.get("passed"):
                    print(f"  [{node_name}] 通过（检查 {v.get('checked_dims', 0)} 个维度）")
                else:
                    print(f"  [{node_name}] 失败: {len(v.get('errors', []))} 项问题，将重试")
                    for err_item in v.get("errors", []):
                        print(f"      - {err_item}")
            elif node_name in ("llm_extract", "gap_analysis", "learning_path"):
                print(f"  [{node_name}] 完成")
            # save_resume / output_result / error_handler 由节点内部打印
    return merged


def _extract_resume_data(file_path: str, requirements: str | None, schema_path: str | None) -> dict:
    """运行提取图，返回最终状态（含 resume_data）。

    Returns:
        {"resume_data": ...} 或 {"error": "..."}
    """
    schema = load_json(schema_path) if schema_path else load_extraction_schema()
    initial = {
        "file_path": str(Path(file_path).resolve()),
        "extraction_schema": schema,
        "user_requirements": requirements,
    }
    print(f"\n  正在处理: {initial['file_path']}")
    final = _stream(extract_graph, initial)
    if final.get("error"):
        return {"error": final["error"]}
    return {"resume_data": final.get("resume_data")}


def _prepare_resume(resume_src: str, requirements: str | None, schema_path: str | None) -> dict:
    """根据输入类型准备简历数据：原始文件→先提取；JSON→直接读取。"""
    suffix = Path(resume_src).suffix.lower()
    if suffix in RAW_SUFFIXES:
        return _extract_resume_data(resume_src, requirements, schema_path)
    return {"resume_json": str(Path(resume_src).resolve())}


# ==================== 子命令 ====================

def cmd_extract(args) -> int:
    result = _extract_resume_data(args.file, args.requirements, args.schema)
    if result.get("error"):
        print(f"\n  [ERROR] {result['error']}\n")
        return 1
    return 0


def cmd_analyze(args) -> int:
    resume = _prepare_resume(args.resume, args.requirements, args.schema)
    if resume.get("error"):
        print(f"\n  [ERROR] {resume['error']}\n")
        return 1

    initial = {
        "resume_data": resume.get("resume_data"),
        "resume_json": resume.get("resume_json"),
        "jd_json": str(Path(args.jd).resolve()),
        "user_requirements": args.requirements,
    }
    final = _stream(analysis_graph, initial)
    if final.get("error"):
        print(f"\n  [ERROR] {final['error']}\n")
        return 1
    return 0


def _run_analysis_worker(
    jd_file: Path,
    resume_data,
    resume_json: str | None,
    requirements: str | None,
) -> dict:
    """单个 JD 的分析任务（线程安全：每个任务独立 initial state）。"""
    jd = load_json(str(jd_file))
    title = jd.get("job_title", jd_file.stem)
    initial = {
        "resume_data": resume_data,
        "resume_json": resume_json,
        "jd_json": str(jd_file.resolve()),
        "user_requirements": requirements,
    }
    try:
        final = _stream(analysis_graph, initial)
    except Exception as exc:
        return {"job_title": title, "file": jd_file.name, "error": str(exc)}
    if final.get("error"):
        return {"job_title": title, "file": jd_file.name, "error": final["error"]}
    return {
        "job_title": title,
        "file": jd_file.name,
        "analysis": final.get("analysis_result"),
        "verify": final.get("verify_result"),
    }


def cmd_batch(args) -> int:
    resume = _prepare_resume(args.resume, args.requirements, args.schema)
    if resume.get("error"):
        print(f"\n  [ERROR] {resume['error']}\n")
        return 1

    jd_dir = Path(args.jd_dir)
    if not jd_dir.is_dir():
        print(f"\n  [ERROR] JD 目录不存在: {jd_dir}\n")
        return 1
    jd_files = sorted(jd_dir.glob("*.json"))
    if not jd_files:
        print("\n  [ERROR] JD 目录下没有 JSON 文件\n")
        return 1

    workers = args.workers or 3
    print(f"\n  批量分析: {len(jd_files)} 个 JD（并行 {workers} 路）")

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _run_analysis_worker,
                jf,
                resume.get("resume_data"),
                resume.get("resume_json"),
                args.requirements,
            )
            for jf in jd_files
        ]
        results = [f.result() for f in futures]

    failed = sum(1 for r in results if r.get("error"))

    # 统一打印各 JD 匹配结论
    print("\n" + "=" * 70)
    print("  批量匹配结论汇总")
    print("=" * 70)
    for r in results:
        if r.get("error"):
            print(f"  [{r['job_title']}] 失败: {r['error']}")
            continue
        match = (r.get("analysis") or {}).get("match", {})
        verdict = "匹配" if match.get("verdict") == "yes" else "不匹配"
        print(f"  [{r['job_title']}] {verdict} - {match.get('reason', '')}")
    print("=" * 70)

    out = ANALYSIS_DIR / "batch_summary.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  [batch] 完成: {len(results) - failed} 成功, {failed} 失败")
    print(f"  [batch] 汇总已保存: {out}\n")
    return 1 if failed else 0


def cmd_rank(args) -> int:
    """六维加权打分初筛（非 LLM）：候选人六维 vs 目录下所有 JD，输出排序表。"""
    resume = _prepare_resume(args.resume, args.requirements, args.schema)
    if resume.get("error"):
        print(f"\n  [ERROR] {resume['error']}\n")
        return 1

    if resume.get("resume_json"):
        resume_data = load_json(resume["resume_json"])
    else:
        resume_data = resume.get("resume_data") or {}
    candidate = resume_data.get("five_dim", {})
    if not candidate:
        print("\n  [ERROR] 简历缺少 five_dim 六维数据\n")
        return 1

    try:
        if args.source == "neo4j":
            jds = load_jds_from_neo4j()
        else:
            jds = load_jds_from_local(args.jd_dir)
    except NotImplementedError as exc:
        print(f"\n  [ERROR] {exc}\n")
        return 1
    except (FileNotFoundError, OSError) as exc:
        print(f"\n  [ERROR] {exc}\n")
        return 1

    weights = None
    if args.weights:
        weights = load_json(args.weights)

    try:
        ranked = rank_jds(candidate, jds, topk=args.topk, weights=weights)
    except ValueError as exc:
        print(f"\n  [ERROR] 权重配置错误: {exc}\n")
        return 1

    # 打印排序表（按显示宽度对齐，中文按 2 列宽计）
    def _pad(text: str, width: int) -> str:
        disp = sum(2 if ord(c) > 0x2E80 else 1 for c in str(text))
        return str(text) + " " * max(0, width - disp)

    labels = {
        "knowledge": "知识",
        "skill": "技术",
        "qualifications": "任职",
        "motivation": "动机",
        "trait": "特质",
        "self_concept": "自我",
    }
    print("\n" + "=" * 100)
    print("  六维加权初筛排名（非 LLM，总分 = Σ 权重×维度覆盖率）")
    print("=" * 100)
    header = "  " + _pad("#", 3) + _pad("岗位名称", 24) + _pad("总分", 8)
    header += "".join(_pad(labels[d], 9) for d in DIMENSION_KEYS)
    print(header)
    print("  " + "-" * 96)
    for idx, item in enumerate(ranked, 1):
        dims = item["dim_scores"]
        row = (
            "  " + _pad(idx, 3) + _pad(item["job_title"], 24)
            + _pad(f"{item['total_score']:.4f}", 8)
            + "".join(_pad(f"{dims[d]['score']:.3f}", 9) for d in DIMENSION_KEYS)
        )
        print(row)
    print("=" * 100)

    out = ANALYSIS_DIR / "rank_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)
    print(f"\n  [rank] 结果已保存: {out}\n")
    return 0




# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(
        prog="resume-agent",
        description="简历提取分析 Agent —— 六维提取 + 差距分析 + 学习路径",
    )
    parser.add_argument("--fast", action="store_true", help="使用 deepseek-v4-flash 快速模型")
    subparsers = parser.add_subparsers(dest="command")

    p_extract = subparsers.add_parser("extract", help="提取简历六维并保存 JSON")
    p_extract.add_argument("file")
    p_extract.add_argument("-r", "--requirements", help="用户重点关注方向")
    p_extract.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径")
    p_extract.add_argument("--fast", action="store_true", help="使用快速模型")

    p_analyze = subparsers.add_parser("analyze", help="简历 vs 单 JD 差距分析 + 学习路径")
    p_analyze.add_argument("-r", "--resume", required=True, help="简历 JSON 或原始 pdf/docx")
    p_analyze.add_argument("-j", "--jd", required=True, help="JD JSON 路径")
    p_analyze.add_argument("--requirements", help="用户重点关注方向")
    p_analyze.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径")
    p_analyze.add_argument("--fast", action="store_true", help="使用快速模型")

    p_batch = subparsers.add_parser("batch-analyze", help="简历 vs 目录下所有 JD 批量分析")
    p_batch.add_argument("-r", "--resume", required=True, help="简历 JSON 或原始 pdf/docx")
    p_batch.add_argument("-j", "--jd-dir", required=True, help="JD JSON 目录")
    p_batch.add_argument("--requirements", help="用户重点关注方向")
    p_batch.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径")
    p_batch.add_argument("--workers", type=int, default=3, help="并行路数（默认 3）")
    p_batch.add_argument("--fast", action="store_true", help="使用快速模型")


    p_rank = subparsers.add_parser("rank", help="六维加权打分初筛（非 LLM）")
    p_rank.add_argument("-r", "--resume", required=True, help="简历 JSON 或原始 pdf/docx")
    p_rank.add_argument("-j", "--jd-dir", required=True, help="JD 目录（含 five_dim 的 JSON 文件）")
    p_rank.add_argument("--requirements", help="用户重点关注方向（仅原始简历文件需要）")
    p_rank.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径（仅原始简历文件需要）")
    p_rank.add_argument("--topk", type=int, default=0, help="只输出前 N 名（0=全部，默认）")
    p_rank.add_argument("--source", choices=["local", "neo4j"], default="local",
                        help="JD 数据来源：local 本地 JSON（默认）/ neo4j 图谱（待实现）")
    p_rank.add_argument("--weights", help="自定义权重 JSON 路径（可选，自动归一化）")

    # 旧用法兼容：python src/main.py samples/test2.pdf → extract samples/test2.pdf
    argv = sys.argv[1:]
    if argv and argv[0] not in ("extract", "analyze", "batch-analyze", "rank", "-h", "--help"):
        argv = ["extract"] + argv

    args = parser.parse_args(argv)

    # --fast 切换模型
    if getattr(args, "fast", False):
        os.environ["DEEPSEEK_MODEL"] = FAST_MODEL
        print(f"  [fast] 使用 {FAST_MODEL} 模型")

    if args.command == "extract":
        sys.exit(cmd_extract(args))
    elif args.command == "analyze":
        sys.exit(cmd_analyze(args))
    elif args.command == "batch-analyze":
        sys.exit(cmd_batch(args))
    elif args.command == "rank":
        sys.exit(cmd_rank(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
