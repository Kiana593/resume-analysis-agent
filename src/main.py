"""入口 —— CLI 驱动：extract / analyze / batch-analyze / rank / stats。

用法示例：
    python src/main.py extract samples/test2.pdf -r "重点关注AI项目经验"
    python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json
    python src/main.py batch-analyze -r results/test2.json -j jds --workers 3
    python src/main.py analyze -r results/test2.json -j jds/01_ic_design.json --fast
    python src/main.py rank -r results/test2.json -j jds --topk 3
    python src/main.py rank -r results/test2.json --source neo4j --topk 5
    python src/main.py rank -r results/test2.json --source neo4j --graph 0.3 --topk 5
    python src/main.py stats --source neo4j
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
from src.retrieval.neo4j_loader import load_jds_from_local
from src.retrieval.graph_match import get_graph_stats
from src.retrieval.role_loader import load_roles_from_neo4j, rank_roles

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


def _extract_resume_data(file_path: str, requirements: str | None, schema_path: str | None, mode: str = "llm") -> dict:
    """运行提取图，返回最终状态（含 resume_data）。

    Args:
        mode: 提取路线 "llm"（默认）或 "rule"。

    Returns:
        {"resume_data": ...} 或 {"error": "..."}
    """
    schema = load_json(schema_path) if schema_path else load_extraction_schema()
    initial = {
        "file_path": str(Path(file_path).resolve()),
        "extraction_schema": schema,
        "user_requirements": requirements,
        "extraction_mode": mode,
    }
    print(f"\n  正在处理: {initial['file_path']}")
    final = _stream(extract_graph, initial)
    if final.get("error"):
        return {"error": final["error"]}
    return {"resume_data": final.get("resume_data")}


def _prepare_resume(resume_src: str, requirements: str | None, schema_path: str | None, mode: str = "llm") -> dict:
    """根据输入类型准备简历数据：原始文件→先提取；JSON→直接读取。

    Args:
        mode: 提取路线 "llm"（默认）或 "rule"（仅原始文件时生效）。
    """
    suffix = Path(resume_src).suffix.lower()
    if suffix in RAW_SUFFIXES:
        return _extract_resume_data(resume_src, requirements, schema_path, mode)
    return {"resume_json": str(Path(resume_src).resolve())}


# ==================== 子命令 ====================

def cmd_extract(args) -> int:
    mode = getattr(args, "mode", "llm")
    result = _extract_resume_data(args.file, args.requirements, args.schema, mode)
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


def cmd_batch(args) -> int:
    resume = _prepare_resume(args.resume, args.requirements, args.schema)
    if resume.get("error"):
        print(f"\n  [ERROR] {resume['error']}\n")
        return 1
    resume_data = resume.get("resume_data")
    resume_json = resume.get("resume_json")

    jd_dir = Path(args.jd_dir)
    jd_files = sorted(jd_dir.glob("*.json"))
    if not jd_files:
        print(f"\n  [ERROR] JD 目录下没有 JSON 文件: {jd_dir}\n")
        return 1

    print(f"\n  批量分析: 简历 vs {len(jd_files)} 个 JD (并行 {args.workers} 路)")
    print("-" * 60)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for jd_path in jd_files:
            initial = {
                "resume_data": resume_data,
                "resume_json": resume_json,
                "jd_json": str(jd_path.resolve()),
                "user_requirements": args.requirements,
            }
            fut = executor.submit(_stream, analysis_graph, initial)
            futures[fut] = jd_path.stem

        for fut in concurrent.futures.as_completed(futures):
            jd_name = futures[fut]
            try:
                final = fut.result()
                if final.get("error"):
                    print(f"  [{jd_name}] 错误: {final['error']}")
                    results.append({"jd": jd_name, "error": final["error"]})
                else:
                    analysis = final.get("analysis_result", {})
                    verdict = analysis.get("match", {}).get("verdict", "unknown")
                    print(f"  [{jd_name}] 完成 — {verdict}")
                    results.append({"jd": jd_name, "verdict": verdict, "analysis": analysis})
            except Exception as exc:
                print(f"  [{jd_name}] 异常: {exc}")
                results.append({"jd": jd_name, "error": str(exc)})

    out_path = ANALYSIS_DIR / "batch_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  批量分析结果已保存: {out_path}\n")
    return 0


def cmd_rank(args) -> int:
    """Role 级别宏观职位匹配 —— 简历 vs 134 个标准职业（Neo4j）。

    每个 Role 通过核心技能覆盖率（final_score 加权）计算匹配得分，
    按得分降序输出最匹配的职业方向。
    """
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
        print("\n  [rank] 从 Neo4j 加载 Role 核心技能 ...")
        roles = load_roles_from_neo4j()
        ranked_roles = rank_roles(candidate, roles, topk=args.topk)
        print(f"  [rank] {len(roles)} 个 Role，保留 Top {len(ranked_roles)}")

        _print_role_table(ranked_roles, title="Role 级别职位匹配排名（核心技能覆盖率）")

        out = ANALYSIS_DIR / "rank_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(ranked_roles, f, ensure_ascii=False, indent=2)
        print(f"\n  [rank] Role 排名已保存: {out}\n")
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"\n  [ERROR] {exc}\n")
        return 1




def _print_role_table(ranked_roles, title: str = "Role 粗排") -> None:
    """打印 Role 排名表。"""
    def _pad(text: str, width: int) -> str:
        disp = sum(2 if ord(c) > 0x2E80 else 1 for c in str(text))
        return str(text) + " " * max(0, width - disp)

    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)
    header = "  " + _pad("#", 3) + _pad("标准职业", 22) + _pad("家族", 16)         + _pad("领域", 16) + _pad("旗下JD", 8) + _pad("命中", 6) + _pad("得分", 8)
    print(header)
    print("  " + "-" * 96)
    for idx, item in enumerate(ranked_roles, 1):
        row = (
            "  " + _pad(idx, 3) + _pad(item["role_name"], 22)
            + _pad(item.get("family_name", ""), 16)
            + _pad(item.get("domain_name", ""), 16)
            + _pad(str(item.get("jd_count", 0)), 8)
            + _pad(f"{item.get('hit_skills', 0)}/{item.get('total_skills', 0)}", 6)
            + _pad(f"{item['score']:.4f}", 8)
        )
        print(row)
    print("=" * 100)


def cmd_stats(args) -> int:
    """查看数据源统计信息。"""
    if args.source == "neo4j":
        print("\n  [stats] Neo4j 图数据库统计 ...")
        try:
            stats = get_graph_stats()
            if stats:
                print(f"  JD 节点数:     {stats.get('jd_count', 0)}")
                print(f"  简历节点数:    {stats.get('resume_count', 0)}")
                print(f"  技能节点数:    {stats.get('skill_count', 0)}\n")
            else:
                print("  [stats] 无法连接 Neo4j，请检查配置\n")
                return 1
        except Exception as exc:
            print(f"  [ERROR] {exc}\n")
            return 1
    else:
        jd_dir = Path(args.jd_dir) if args.jd_dir else Path("jds")
        if not jd_dir.is_dir():
            print(f"\n  [stats] JD 目录不存在: {jd_dir}\n")
            return 1
        files = list(jd_dir.glob("*.json"))
        print(f"\n  [stats] 本地数据源: {jd_dir}")
        print(f"  JD 文件数:     {len(files)}")
        # 统计结果文件
        if RESULTS_DIR.is_dir():
            result_files = list(RESULTS_DIR.glob("*.json"))
            analysis_files = list(ANALYSIS_DIR.glob("*.json")) if ANALYSIS_DIR.is_dir() else []
            print(f"  提取结果数:    {len(result_files)}")
            print(f"  分析结果数:    {len(analysis_files)}\n")
    return 0


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(
        prog="resume-agent",
        description="简历提取分析 Agent —— 六维提取 + 差距分析 + 学习路径 + Neo4j图匹配",
    )
    parser.add_argument("--fast", action="store_true", help="使用 deepseek-v4-flash 快速模型")
    subparsers = parser.add_subparsers(dest="command")

    p_extract = subparsers.add_parser("extract", help="提取简历六维并保存 JSON")
    p_extract.add_argument("file")
    p_extract.add_argument("-r", "--requirements", help="用户重点关注方向")
    p_extract.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径")
    p_extract.add_argument("--mode", choices=["llm", "rule"], default="llm",
                           help="提取路线：llm DeepSeek 提取（默认）/ rule 脚本规则提取")
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

    p_rank = subparsers.add_parser("rank", help="Role 级别职位匹配（Neo4j 核心技能覆盖率）")
    p_rank.add_argument("-r", "--resume", required=True, help="简历 JSON 或原始 pdf/docx")
    p_rank.add_argument("--requirements", help="用户重点关注方向（仅原始简历文件需要）")
    p_rank.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径（仅原始简历文件需要）")
    p_rank.add_argument("--topk", type=int, default=0, help="只输出前 N 名（0=全部，默认）")

    p_stats = subparsers.add_parser("stats", help="查看数据源统计（本地 / Neo4j）")
    p_stats.add_argument("--source", choices=["local", "neo4j"], default="local",
                         help="数据来源（默认 local）")
    p_stats.add_argument("-j", "--jd-dir", default="jds", help="JD 目录（--source local 时使用）")

    # 旧用法兼容：python src/main.py samples/test2.pdf → extract samples/test2.pdf
    argv = sys.argv[1:]
    if argv and argv[0] not in ("extract", "analyze", "batch-analyze", "rank", "stats", "-h", "--help"):
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
    elif args.command == "stats":
        sys.exit(cmd_stats(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

