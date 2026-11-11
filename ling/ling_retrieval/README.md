# ling_retrieval · 离线检索原型

K 轨道（代码库检索）的离线实验，不接入 VS Code 扩展。

## 能力

- Python：标准库 `ast` 按函数/方法/类切 chunk
- TS/JS：轻量文本扫描（顶层 function/class/箭头函数 + 类内方法），不依赖完整 parser
- SQLite FTS5 BM25：符号 / 签名 / 注释 / 正文四列，权重 `5 / 2 / 2 / 1`
- 向量检索：OpenAI 兼容 `/embeddings`，向量以 float32 BLOB 存在同一个 SQLite
- 混合检索：BM25 top N + 向量 top N，RRF（k=60）融合
- 与 grep-only 对比：recall@5、recall@10、MRR@10、查询延迟、索引大小
- 默认跳过 `tests/`，`--include-tests` 可以打开

## 运行

Python 工作区：

```powershell
cd ling
python -m ling_retrieval.eval `
  --workspace . `
  --queries ling_retrieval/eval_queries.json `
  --out ling_retrieval/report.json
```

TypeScript 工作区：

```powershell
python -m ling_retrieval.eval `
  --workspace ../ling-vscode `
  --queries ling_retrieval/eval_queries_ts.json `
  --out ling_retrieval/report-ts.json
```

带向量检索（需要支持 embeddings 的 key；DeepSeek 没有此接口）：

```powershell
$env:LING_EMBEDDING_MODEL = "text-embedding-v4"
$env:LING_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:LING_EMBEDDING_API_KEY = "sk-..."

python -m ling_retrieval.eval `
  --workspace . `
  --queries ling_retrieval/eval_queries.json `
  --out ling_retrieval/report.json
```

也可以改用硅基流动：`LING_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1`，
模型 `BAAI/bge-m3`。设置后报告会同时输出 BM25 / grep / vector / RRF 四组指标。

## 接入

`ling_agent/tools/search.py` 通过 `CodeIndex` 暴露 `search_codebase` 工具：

- 索引：`<workspace>/.ling/code-index/index.sqlite`
- 首次搜索构建，之后按 mtime/size 增量更新
- 也可以手动触发：Settings → Code index → Build code index，或命令面板
  `Ling: Build Code Index`（RPC `index.build`）
- 支持 `top_k`、`language`、`path_prefix`
- tests 路径默认降权 8.0
- 配置了 embedding 时自动启用混合检索，结果里的 `mode` 为 `hybrid` 或 `bm25`
- 向量按 `content_hash` 复用：只改一个函数，其它函数的向量不重算
- 首次搜索会同步补算未索引的 chunk（单次上限 2000），之后按 `content_hash` 增量更新
- 切换 embedding 模型会清空旧向量并重建
- embedding 调用失败时自动退回 BM25，不影响工具返回结果
- 尚未做后台建索引

## 数据集格式

JSON 数组，每项：

```json
{
  "query": "parse PDF page text",
  "relevant": [
    {"path": "ling_agent/tools/documents.py", "symbol": "_extract_pdf"}
  ]
}
```

- `path` 相对于 `--workspace`
- 一个需求可以标多个相关函数（入口 + 私有 helper）
- `symbol` 省略时按文件级匹配

## 当前结果（2026-11-11）

默认排除 tests，每个仓库 30 条 query：

向量使用阿里云百炼 `text-embedding-v3`（1024 维），第一批数据是 2026-11-11 的 BM25/grep
基线，第二批是 2026-11-12 加向量后的对比。

Python（`ling/`）：

```text
66 files, 679 chunks, build 0.049s
index: 1544 KiB (无向量) / 4604 KiB (含向量), embedding build 31.98s
BM25   recall@5=0.800 recall@10=0.906 MRR@10=0.776 p50=0.41ms
grep   recall@5=0.700 recall@10=0.867 MRR@10=0.523 p50=16.95ms
vector recall@5=0.939 recall@10=0.939 MRR@10=0.894 p50=6.24ms
RRF    recall@5=0.878 recall@10=0.967 MRR@10=0.903
```

TypeScript（`ling-vscode/`）：

```text
82 files, 400 chunks, build 0.035s
index: 1132 KiB (无向量) / 2932 KiB (含向量), embedding build 19.17s
BM25   recall@5=0.767 recall@10=0.867 MRR@10=0.724 p50=0.30ms
grep   recall@5=0.767 recall@10=0.800 MRR@10=0.449 p50=14.23ms
vector recall@5=0.867 recall@10=0.933 MRR@10=0.774 p50=3.38ms
RRF    recall@5=0.867 recall@10=0.933 MRR@10=0.747
```

60 条 query 合并（两仓库各 30 条）：

```text
BM25   recall@5≈0.784 recall@10≈0.887 MRR@10≈0.750
grep   recall@5≈0.734 recall@10≈0.834 MRR@10≈0.486
vector recall@5≈0.903 recall@10≈0.936 MRR@10≈0.834
RRF    recall@5≈0.873 recall@10≈0.950 MRR@10≈0.825
```

结论：

- 自然语言 query 上，向量检索比 BM25 高约 0.10–0.14 recall@5，MRR 也更高；
  “truncate event log after sequence”“delete events from SQLite”这类查
  BM25 只能排到第 4–7 位的 query，向量直接排第 1。
- RRF 的 recall@10 最高（0.950），说明两路互补：一路漏掉的，另一路经常能捞回来。
- RRF 的 MRR 不一定高于纯向量：TS 上纯向量 0.774，RRF 0.747，因为 BM25 的错答案
  也拿到了融合票。等权 RRF 不是最优，之后可以试加权。
- BM25 仍然在精确符号上更强，例如 “resolve bundled skills root”、
  “build sidecar environment variables”；grep 也有个别反超。
- 向量检索的查询延迟是本地余弦（不含网络请求）；首次建索引 679 chunk 约 32s。

索引从 704 KiB 涨到 1544 KiB，因为 `chunk_meta` 现在同时保存原始正文（`body_text`），
这样补算向量时不需要重新读文件；再加 1024 维 float32 向量后是 4604 KiB。

## 边界

- TS/JS 是轻量文本扫描，不是完整 parser；多行签名已支持，但复杂泛型/对象返回类型可能切错
- grep baseline 是单次 OR 扫描后映射到 chunk，不是真实 agent 的多轮 grep
- 数据集是手工种子集，规模小，结论只用于指导方向
- 向量是 float32 BLOB + 暴力余弦，适合几千到几万 chunk；再大需要 ANN 索引
- 首次搜索同步补算向量，仓库很大时会明显变慢；后台建索引还没做
- 生成物 `report*.json` 已在 `.gitignore` 中
