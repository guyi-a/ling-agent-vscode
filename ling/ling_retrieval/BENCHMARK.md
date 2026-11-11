# 代码检索评测结果

记录日期：2026-11-12

## 条件

- 数据集：两仓库各 30 条人工 query，默认排除 `tests/`
  - Python：`ling/`，66 文件，679 chunk
  - TypeScript：`ling-vscode/`，82 文件，400 chunk
- BM25：SQLite FTS5，四列权重 `5 / 2 / 2 / 1`，tests 路径降权 8.0
- grep：单次 OR 扫描后映射到 chunk
- 向量：阿里云百炼 `text-embedding-v3`，1024 维 float32；本地暴力余弦
- 融合：BM25 top 50 + 向量 top 50，RRF（k=60），等权
- top-k = 10

## 结果

| 指标 | BM25 | grep | vector | RRF |
|---|---|---|---|---|
| Python recall@5 | 0.800 | 0.700 | **0.939** | 0.878 |
| Python recall@10 | 0.906 | 0.867 | 0.939 | **0.967** |
| Python MRR@10 | 0.776 | 0.523 | 0.894 | **0.903** |
| TS recall@5 | 0.767 | 0.767 | **0.867** | 0.867 |
| TS recall@10 | 0.867 | 0.800 | 0.933 | **0.933** |
| TS MRR@10 | 0.724 | 0.449 | **0.774** | 0.747 |
| 60 条合并 recall@5 | 0.784 | 0.734 | **0.903** | 0.873 |
| 60 条合并 recall@10 | 0.887 | 0.834 | 0.936 | **0.950** |
| 60 条合并 MRR@10 | 0.750 | 0.486 | **0.834** | 0.825 |

延迟（不含 embedding 网络请求）：

| | BM25 p50 | grep p50 | vector p50 |
|---|---|---|---|
| Python | 0.41ms | 16.95ms | 6.24ms |
| TypeScript | 0.30ms | 14.23ms | 3.38ms |

索引体积：

| | 无向量 | 含向量 | 建向量耗时 |
|---|---|---|---|
| Python (679 chunk) | 1544 KiB | 4604 KiB | 31.98s |
| TypeScript (400 chunk) | 1132 KiB | 2932 KiB | 19.17s |

## 结论

- 自然语言 query 上，向量比 BM25 高 0.10–0.14 recall@5；“delete events from SQLite”、
  “truncate event log after sequence”这类 BM25 排第 4–7 位或找不到的 query，向量直接第 1。
- RRF 的 recall@10 最高（0.950），说明两路互补：一路漏掉的，另一路经常能捞回来。
- 等权 RRF 的 MRR 不一定高于纯向量：TS 上纯向量 0.774，RRF 0.747，因为 BM25 的错答案
  也拿到了融合票。加权 RRF 是下一步值得试的方向。
- BM25 仍在精确符号上更强，例如 “resolve bundled skills root”、
  “build sidecar environment variables”；grep 也有个别反超。
- 向量查询延迟是本地余弦（不含网络）；首次建索引是主要成本。

## 复现

```powershell
cd E:\毕业论文\ling

$env:LING_EMBEDDING_MODEL = "text-embedding-v3"
$env:LING_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:LING_EMBEDDING_API_KEY = "sk-..."

python -m ling_retrieval.eval --workspace . `
  --queries ling_retrieval/eval_queries.json --out ling_retrieval/report-vector.json

python -m ling_retrieval.eval --workspace ../ling-vscode `
  --queries ling_retrieval/eval_queries_ts.json --out ling_retrieval/report-ts-vector.json
```

原始报告 `report*.json` 已在 `.gitignore` 中，不提交。
