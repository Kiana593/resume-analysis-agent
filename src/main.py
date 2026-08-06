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
from src.retrieval.scoring import rank_jds, DIMENSION_KEYS
from src.retrieval.neo4j_loader import load_jds_from_local, load_jds_from_neo4j
from src.retrieval.graph_match import get_graph_stats
from src.retrieval.role_loader import load_roles_from_neo4j, rank_roles, load_jds_by_roles
from src.retrieval.csv_loader import enrich_jds_with_csv

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
    """六维加权打分初筛（非 LLM）：支持两阶段（Role 粗排 + JD 细排）。

    --source local：本地 jds/ JSON，单阶段 JD 打分（原行为）。
    --source neo4j：两阶段：
        阶段 1  Role 粗排（核心技能 + final_score 权重覆盖率）
        阶段 2  候选 Role 旗下 JD 细排（优先 CSV 完整六维）
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

    weights = None
    if args.weights:
        weights = load_json(args.weights)

    try:
        if args.source == "neo4j":
            return _cmd_rank_neo4j(args, candidate, weights)
        return _cmd_rank_local(args, candidate, weights)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"\n  [ERROR] {exc}\n")
        return 1


def _cmd_rank_local(args, candidate, weights) -> int:
    """本地模式：jds/ 目录单阶段打分（原行为）。"""
    jds = load_jds_from_local(args.jd_dir)

    # 打分 + 排序
    try:
        ranked = rank_jds(candidate, jds, topk=args.topk, weights=weights)
    except ValueError as exc:
        print(f"\n  [ERROR] 权重配置错误: {exc}\n")
        return 1

    _print_jd_table(ranked, title="六维加权初筛排名（本地 JSON，总分 = Σ 权重×维度覆盖率）")
    out = ANALYSIS_DIR / "rank_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)
    print(f"\n  [rank] 结果已保存: {out}\n")
    return 0


def _cmd_rank_neo4j(args, candidate, weights) -> int:
    """Neo4j 两阶段：Role 粗排 → 候选 Role 旗下 JD 细排（CSV 增强）。"""
    stage = getattr(args, "stage", "both")

    # ============ 阶段 1：Role 粗排 ============
    print(f"\n  [阶段1] 从 Neo4j 加载 Role 核心技能并粗排 ...")
    roles = load_roles_from_neo4j()
    ranked_roles = rank_roles(candidate, roles, topk=args.role_topk)
    print(f"  [阶段1] {len(roles)} 个 Role，保留 Top {len(ranked_roles)}")

    if stage in ("role", "both"):
        _print_role_table(ranked_roles)

    if stage == "role":
        # 仅输出 Role 排名
        out = ANALYSIS_DIR / "rank_result.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(ranked_roles, f, ensure_ascii=False, indent=2)
        print(f"\n  [rank] Role 排名已保存: {out}\n")
        return 0

    # ============ 阶段 2：候选 Role 旗下 JD 细排 ============
    role_names = [r["role_name"] for r in ranked_roles]
    print(f"\n  [阶段2] 加载 {len(role_names)} 个候选 Role 旗下的 JD ...")
    jds = load_jds_by_roles(role_names)

    # CSV 增强：优先用完整能力分析结果
    csv_dir = getattr(args, "csv_dir", "") or ""
    if csv_dir:
        try:
            jds, matched, total = enrich_jds_with_csv(jds, csv_dir)
            print(f"  [阶段2] CSV 完整六维增强: {matched}/{total} 个 JD 匹配成功")
        except (FileNotFoundError, OSError) as exc:
            print(f"  [阶段2] CSV 加载失败，回退图谱技能: {exc}")

    if not jds:
        print("\n  [ERROR] 候选 Role 下没有 JD\n")
        return 1

    # 打分 + 排序
    try:
        ranked = rank_jds(candidate, jds, topk=args.topk, weights=weights)
    except ValueError as exc:
        print(f"\n  [ERROR] 权重配置错误: {exc}\n")
        return 1

    _print_jd_table(ranked, title="六维加权细排（候选 Role 内，总分 = Σ 权重×维度覆盖率）")

    # 保存两阶段结果
    out = ANALYSIS_DIR / "rank_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {"roles": ranked_roles, "jds": ranked},
            f, ensure_ascii=False, indent=2,
        )
    print(f"\n  [rank] 两阶段结果已保存: {out}\n")
    return 0


def _print_role_table(ranked_roles) -> None:
    """打印 Role 粗排表。"""
    def _pad(text: str, width: int) -> str:
        disp = sum(2 if ord(c) > 0x2E80 else 1 for c in str(text))
        return str(text) + " " * max(0, width - disp)

    print("\n" + "=" * 100)
    print("  阶段 1：Role 粗排（权重覆盖率 = Σ命中技能×final_score / Σfinal_score）")
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


def _print_jd_table(ranked, title) -> None:
    """打印 JD 排名表（六维）。"""
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
    print(f"  {title}")
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

    p_rank = subparsers.add_parser("rank", help="六维加权打分初筛（非 LLM）+ Neo4j 图匹配增强")
    p_rank.add_argument("-r", "--resume", required=True, help="简历 JSON 或原始 pdf/docx")
    p_rank.add_argument("-j", "--jd-dir", default="jds",
                        help="JD 目录（--source local 时需要，默认 jds/；--source neo4j 时忽略）")
    p_rank.add_argument("--requirements", help="用户重点关注方向（仅原始简历文件需要）")
    p_rank.add_argument("-s", "--schema", help="自定义提取 schema JSON 路径（仅原始简历文件需要）")
    p_rank.add_argument("--topk", type=int, default=0, help="只输出前 N 名（0=全部，默认）")
    p_rank.add_argument("--source", choices=["local", "neo4j"], default="local",
                        help="JD 数据来源：local 本地 JSON（默认）/ neo4j 图谱")
    p_rank.add_argument("--weights", help="自定义权重 JSON 路径（可选，自动归一化）")
    p_rank.add_argument("--graph", type=float, default=0.0,
                        help="图匹配融合权重 [0,1]（需 Neo4j + resume-graph-match；0=纯文本，默认）")
    p_rank.add_argument("--min-features", type=int, default=6,
                        help="仅 --source neo4j：过滤六维总条目数少于该值的 JD（默认 6，0=不过滤）")
    p_rank.add_argument("--role-topk", type=int, default=10,
                        help="阶段1 Role 粗排保留数量（默认 10，0=全部）")
    p_rank.add_argument("--stage", choices=["both", "role", "jd"], default="both",
                        help="输出阶段：both 两阶段都输出（默认）/ role 仅 Role 粗排 / jd 仅 JD 细排")
    p_rank.add_argument("--csv-dir", default="",
                        help="提取后原始数据 CSV 目录；提供后用 CSV 完整六维增强 JD（默认自动回退图谱技能）")

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

