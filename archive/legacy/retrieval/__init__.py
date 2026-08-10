"""检索初筛模块 —— 六维加权打分 + Neo4j 数据接入。

阶段1 初筛：纯算法，不调用 LLM；
- scoring: 六维加权打分与排序
- neo4j_loader: 本地/Neo4j 双模式 JD 加载，简历写入与查询
- graph_match: Neo4j 图谱匹配（对接 resume-graph-match）
"""