# ling_eval

Ling 的 headless 评测入口。不启动 VS Code，直接拉起 sidecar、提交一个 prompt、等待 run 结束，并导出相对 base commit 的 git patch。

当前包含 headless runner、SWE-bench 适配器、Docker / 官方镜像运行、官方 harness 判分和 token 汇总。评测数据与运行产物放在仓库外（`eval-data/`、`logs/` 已 gitignore），这个包只保留代码。

## 定位

这套评测用于发现 harness 缺少的工具和能力，不以刷分为目标。SWE-bench 的隐藏测试往往要求非常具体的实现；分数低不直接等于 harness 有缺陷，优先看 record 里的工具调用、失败阶段和 token 消耗。

## 使用

在 `ling/` 目录下：

```powershell
$env:LING_API_KEY = "..."   # 或 DEEPSEEK_API_KEY
python -m ling_eval `
  --workspace E:\eval\repo `
  --prompt-file E:\eval\issue.md `
  --output E:\eval\result.json `
  --event-log E:\eval\events.jsonl
```

常用参数：

- `--model` / `--base-url`：覆盖 `LING_MODEL` / `LING_API_BASE_URL`
- `--timeout`：整轮超时，默认 1800 秒
- `--max-steps`：覆盖 `LING_MAX_STEPS`
- `--sidecar`：指定已打包的 sidecar 可执行文件；默认用 `python ling_sidecar_entry.py`
- `--no-isolate`：不复用临时 storage/skills/memory/MCP 隔离目录（调试用）

`result.json` 包含 `status`、`baseRevision`、`patch`、`assistantText`、`toolCalls`、`runs`、`stderrTail` 等字段；`events.jsonl` 保存 sidecar 的 `ui.event` 通知。

## 运行行为

- 设置 `LING_COMPUTER_ENABLED=0`，评测时不加载 Computer Use。
- 使用 `approvalMode=auto`；若仍出现审批，自动批准非 Computer Use 接管，拒绝 Computer Use；`ask_user` 自动取消。
- 默认把 storage、skills、global skills、memory、MCP 配置和状态放到临时目录，避免污染用户环境。
- 等待 `session.snapshot` 中最新 run 进入 `completed` / `cancelled` / `error`，或 sidecar 发出 `run.failed` / `run.cancelled`。
- patch 用 `git diff <base>` 导出，包含新文件；`git add -N` 只标记 intent-to-add，不改内容；排除 `.agents/` 运行时文件。
- 超时会调用 `session.cancel`，等待最多 10 秒再退出。

## SWE-bench

评测数据不进 git。建议在仓库外建一个可随时删除的目录，下面用 `$EvalRoot` 表示：

```powershell
$EvalRoot = "E:\eval"
mkdir $EvalRoot
```

需要 `SWE-bench/SWE-bench_Lite` 的 test split（包含 `image`、`eval_script`、`log_parser`、`eval_type` 字段）。用 `datasets` 转成 JSONL：

```powershell
python -m pip install datasets
python -c "from datasets import load_dataset; load_dataset('SWE-bench/SWE-bench_Lite', split='test').to_json(r'$EvalRoot\swe-bench-lite.jsonl', force_ascii=False)"
```

跑一条或一批：

```powershell
cd ling
python -m ling_eval.swebench `
  --instances "$EvalRoot\swe-bench-lite.jsonl" `
  --limit 20 `
  --predictions "$EvalRoot\predictions.jsonl" `
  --records "$EvalRoot\records" `
  --workdir "$EvalRoot\runs" `
  --repo-cache "$EvalRoot\repos" `
  --model deepseek-flash `
  --git-proxy http://127.0.0.1:7877 `
  --git-ssl-backend schannel
```

通用 Linux 镜像不随仓库分发；需要时在仓库根目录构建：

```powershell
docker build -t ling-eval-base -f ling/ling_eval/Dockerfile ling
```

- 只把 `problem_statement` 交给 Ling，`patch` / `test_patch` / `FAIL_TO_PASS` 不进入 prompt。
- 每个 instance 先 clone 到 repo cache（bare），再 clone 到独立 workspace，checkout `base_commit`；跑完输出 `predictions.jsonl`（`instance_id` / `model_patch` / `model_name_or_path`）和逐条 record。
- `--resume` 会跳过 predictions 里已有的 instance；`--keep-workspaces` 保留工作区排查。
- 默认在宿主机跑 sidecar；加 `--docker-image ling-eval-base` 可把 workspace 挂进通用 Linux 容器。
- 加 `--official-images` 会使用数据集里的官方实例镜像（`/testbed`，依赖齐全），不再 clone 到宿主机；本地缺失时通过 `--docker-mirror`（默认 daocloud）拉取并 tag 成官方名。Ling 用 `/opt/miniconda3/bin/python` 启动，agent 的命令 PATH 指向 testbed 的 Python 3.9 环境。容器内先给 Ling 装 `requirements.txt`，再跑。适合正式产出预测。
- `--max-tokens N` 按 session DB 里的 assistant usage 统计，超过 N 就取消本轮；record / result 里会写 `usage`（input/output/cache/total/modelCalls）。`--max-steps` 仍然按模型步数限制。
- 当前已知：`psf__requests-1963` 这类旧仓库的 `PASS_TO_PASS` 含 `test_pyopenssl_redirect` / `test_stream_timeout`，官方镜像里旧 `cacert.pem` 会让 gold patch 也失败；这不是 Ling 的 patch 问题。

## 官方判分

SWE-bench harness 建议装在独立 venv（不要污染 Ling 环境）：

```powershell
python -m venv "$EvalRoot\venv-swebench"
& "$EvalRoot\venv-swebench\Scripts\python.exe" -m pip install swebench datasets -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Windows 上 harness 会把 `eval.sh` / `patch.diff` 写成 CRLF，导致 Linux 容器里路径和 patch 失效。需要把 `$EvalRoot\venv-swebench\Lib\site-packages\swebench\harness\run_evaluation.py` 两处 `write_text(...)` 加上 `newline="\n"`；否则 gold 也会 unresolved。

判分：

```powershell
$env:PYTHONUTF8 = "1"
& "$EvalRoot\venv-swebench\Scripts\python.exe" -m swebench.harness.run_evaluation `
  -d "$EvalRoot\swe-bench-lite.jsonl" `
  -i psf__requests-1963 `
  -p "$EvalRoot\predictions.jsonl" `
  -id smoke `
  --max_workers 1 `
  --report_dir "$EvalRoot\reports"
```

报告在 `$EvalRoot/reports/<model_name>.<run_id>.json`，逐实例日志在仓库根目录的 `logs/run_evaluation/<run_id>/`（已 gitignore）。

汇总分数和 token：

```powershell
python -m ling_eval.summarize `
  --records "$EvalRoot\records" `
  --report "$EvalRoot\reports\ling-agent.smoke.json" `
  --run-id smoke
```

输出 `resolved / submitted`、百分比、总 input/output tokens、逐题状态和 patch 是否应用。`--report` 的文件名要对应 `--report_dir` 下实际生成的 `<model_name>.<run_id>.json`。

## 测试

```powershell
python -m pytest tests/test_ling_eval_headless.py tests/test_ling_eval_swebench.py -q
```

`test_ling_eval_headless.py` 使用 `tests/fake_eval_sidecar.py` 模拟 sidecar，覆盖完成、审批、失败和超时取消四条路径；`test_ling_eval_swebench.py` 覆盖实例加载、隐藏字段隔离和本地仓库 patch 导出。
