# -*- coding: utf-8 -*-
"""用 LLM 提取的 faircv #0 简历跑两阶段 rank（LLM 提取 vs 规则提取对比）。

用法:
    python tools/run_rank_llm_vs_rule.py [提取后原始数据CSV目录]

CSV 目录用于将图谱 JD 增强为完整六维（--csv-dir 等价参数）。
"""
import sys, json, io, os, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from dotenv import load_dotenv
load_dotenv()
from src.retrieval.role_loader import load_roles_from_neo4j, rank_roles, load_jds_by_roles
from src.retrieval.csv_loader import enrich_jds_with_csv
from src.retrieval.scoring import rank_jds

if len(sys.argv) < 2:
    print("用法: python tools/run_rank_llm_vs_rule.py [提取后原始数据CSV目录]")
    sys.exit(1)
CSV_DIR = sys.argv[1]

# LLM 提取版
llm_path = glob.glob(os.path.join("results", "faircv_000_*20260806_161312.json"))[0]
with io.open(llm_path, encoding="utf-8") as f:
    llm_data = json.load(f)
cand_llm = llm_data["five_dim"]

# 规则提取版
rule_path = os.path.join("samples", "faircv_fivedim", "000_后端开发工程师.json")
with io.open(rule_path, encoding="utf-8") as f:
    rule_data = json.load(f)
cand_rule = rule_data["five_dim"]

roles = load_roles_from_neo4j()

for tag, cand in [("LLM提取", cand_llm), ("规则提取", cand_rule)]:
    print("=" * 70)
    print(f"=== {tag} ===")
    print("skill:", json.dumps(cand.get("skill", [])[:8], ensure_ascii=False))
    print("knowledge:", json.dumps(cand.get("knowledge", [])[:5], ensure_ascii=False))
    ranked_roles = rank_roles(cand, roles, topk=5)
    print("\n-- Role 粗排 Top5 --")
    for rr in ranked_roles:
        print(f"  {rr['role_name']}: {rr['score']:.3f} ({rr['hit_skills']}/{rr['total_skills']})")
    role_names = [r["role_name"] for r in ranked_roles]
    jds = load_jds_by_roles(role_names)
    jds, matched, total = enrich_jds_with_csv(jds, CSV_DIR)
    ranked = rank_jds(cand, jds, topk=5)
    print(f"\n-- JD 细排 Top5 (CSV增强 {matched}/{total}) --")
    for jd in ranked:
        print(f"  {jd['job_title']}: {jd['total_score']:.3f}")
    print()