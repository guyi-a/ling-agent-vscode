# Ling — 项目说明

Ling 是一套本地运行的 agent harness，通过 VS Code 扩展交付。它负责工具、审批、技能和上下文，让用户留在编辑器里继续工作。仓库由两半组成：
TypeScript 扩展是 VS Code 前端，Python sidecar 是 harness 本体，负责 agent、工具、检索与持久化。

- `ling-vscode/`：VS Code 扩展（`extension.ts`）+ React 19 webview + 打包脚本。
- `ling/`：Python 包（sidecar）。**没有 pyproject / ruff / mypy 配置**，依赖在 `requirements.txt`，包括 `httpx`、`mcp`、`pypdf`、`websockets`。
- `.agents/skills/`：内置技能的**唯一源**（`pdf`、`pptx`、`docx`、`inline-visualization`、`browser-bridge`、`frontend-design`、`computer-use`、`ling-self`），构建时复制到 `ling-vscode/resources/skills/`（该目录被 gitignore）。
- `.agents/{memory,code-index,plans,attachments}/`：运行时状态，只有 `skills` 和 `memory` 应纳入版本控制。
- 记忆分两级：**工作区记忆**在 `.agents/memory/*.md`（随仓库走，当前为空），**全局记忆**在 `%APPDATA%\Ling\memory\*.md`（`LING_GLOBAL_MEMORY_ROOT` 可覆盖，没有 `APPDATA` 时退回 `~/.agents/memory/`）。跨项目的稳定偏好放全局，项目约定放工作区，同名以工作区为准。
- `~/.agents/skills/`：用户全局技能库。插件启动时把内置技能同步到这里（SKILL.md 标 `source: ling`），升级只覆盖未本地修改的托管技能；加载优先级为内置 > 全局无来源 > 工作区。

## 分层

Python 侧从下往上：

1. `ling_ai/` — 模型无关的 `types.py`、`events.py`、`stream.py`；两个 provider 适配器：`adapters/openai_completions.py`（OpenAI Chat Completions，默认 `deepseek-flash`）与 `adapters/anthropic_messages.py`（Anthropic Messages，默认 `deepseek-flash`）。协议由 `LING_API_PROTOCOL` 选择，默认 `openai-completions`。
2. `ling_core/` — 通用 `Agent` 循环（`max_steps`、并行工具、`hooks.py` 的 pre/post tool hooks、`tools.py` 的 `ToolRegistry`）。不含任何产品逻辑。
3. `ling_agent/` — 产品层：`session.py` 开会话并提交事件、`approvals.py` 审批策略、`tools/*`（files / search / commands / documents / computer / browser / web / subagents / skills / memory / plan / spill）、`computer/` Windows 桌面控制服务与 helper（统一 observation、UIA、截图、输入、启动、控制 overlay、审计）、`command_process.py` 命令进程、输出与超时提示、`command_calls.py` 命令前台/后台状态机（模型侧 `background: true` 与手动转后台）、`background_tasks.py` 后台任务调度、`compaction/` 上下文压缩、`storage.py` SQLite 历史、`workspace.py` 路径沙箱。
4. `ling_rpc/` — stdio 上的 NDJSON JSON-RPC（`PROTOCOL_VERSION = 1`）。`server.py` 按 method 字符串分发：`initialize` `ping` `shutdown` `session.*` `changes.*` `mcp.*` `background.*` `tool.background` `approval.resolve` `question.answer` `memory.*` `comment.generate` `index.*`；`ui_events.py` 把事件推给前端。
5. 侧挂模块：`ling_retrieval/`（BM25 + 向量 RRF，带 `eval.py` 与评测报告）、`ling_comment/`（docstring / JSDoc 生成）、`ling_eval/`（headless 评测与 SWE-bench 适配，不属于 sidecar 运行时）。

TypeScript 侧：`src/extension.ts` 是入口，`src/sidecar/SidecarClient.ts` 拉起 Python 并收发 RPC，`src/diffController.ts` 负责原生 diff 审批，`webview/src/state/timelineReducer.ts` 是前端唯一的状态机。webview 构建产物在 `ling-vscode/media/webview/`（gitignore）。

## 命令

Python（在 `ling/` 目录下执行，需要 `python` 在 PATH，或设 `LING_PYTHON_PATH`）：

```powershell
cd ling
python -W error -m tests.test_ling_agent_approvals   # 单文件：每个测试文件都能直接运行
python -m pytest tests -q                            # 全量（pytest 已可用）
$env:LING_LIVE_ANTHROPIC="1"; python -m pytest tests/test_anthropic_live.py -q -s  # DeepSeek Anthropic 实网，需 key
```

测试文件有两类写法，都要保持可用：多数文件是模块级 `test_*` 函数 + `if __name__ == "__main__":` 逐个调用；`test_ling_rpc*.py`、`test_ling_comment.py` 用 `unittest.TestCase`，因为 `npm test` 只用标准库 `unittest` 跑这三个模块。

扩展（在 `ling-vscode/` 下执行）：

```powershell
npm install
npm run typecheck      # tsc --noEmit（扩展 + webview 两套 tsconfig）
npm run build          # typecheck + esbuild 扩展 + vite webview
npm test               # typecheck + 8 个 esbuild 测试脚本 + build，末尾会跑 Python RPC 测试
npm run build:sidecar  # PyInstaller --onefile -> resources/sidecar/ling-sidecar.exe（需 pip install pyinstaller）
npm run package:vsix   # 只支持 --target win32-x64
npm run sync:skills    # .agents/skills -> resources/skills（vscode:prepublish 自动执行）
```

调试扩展：用 VS Code 打开 `ling-vscode/`，按 `F5` 启动 Extension Development Host。

## 约定

- Python：4 空格缩进，`from __future__ import annotations`，Google 风格 docstring，中文只在面向用户的提示里出现。
- TypeScript：prettier 风格（双引号、行尾逗号、100 列左右），无 lint 步骤，靠 `npm run typecheck` 把关。
- 用户可见文案（README、CHANGELOG、UI 字符串）用中文；代码注释用英文。
- 任何写文件 / 执行命令都必须经过 `ling_agent/approvals.py` 的策略，不要在工具里直接落盘。
- 命令白名单在 `.agents/settings.local.json`（本地、gitignored）；审批 UI 的“以后不再问”写入 `permissions.allowCommands`，危险命令永远不适用。
- 修改已有文件前必须先 `read_file`；`edit_file` 和覆盖已有文件的 `write_file` 会校验会话内读取记录和文件 mtime/size，外部修改后要求重读。
- 工具输出上限：超过 12,000 字符写入 `.agents/spills/`，单条结果上限 50,000 字符。
- `run_command` 默认前台，受 Command timeout 限制（默认 3600 秒）。长任务用 `background: true`；后台命令返回 task id 和 `.agents/background/<task-id>.log`，完成时自动通知并续接会话。只有结果出现 `mode: background` 才能说命令在后台运行。
- 版本号同时更新 `ling-vscode/package.json` 和 `CHANGELOG.md`。

## 容易踩的坑

- **路径大小写**：`vite.config.ts` 里对 `__dirname` 做首字母大写处理不是多余代码 —— 混用 `e:/` 和 `E:/` 会让 Rollup 打进两份 React，直接白屏。
- **测试里的路径断言**：CI 是 Windows，历史上有两次提交专门修这个（`Fix memory path assertion on Windows CI`）——比较路径前两边都要 `resolve()`。
- **文档读取**只支持 PDF / DOCX / PPTX，不支持 `.doc` / `.ppt` / `.xlsx`，也没有 OCR。
- **`run_command` 的改动不产生 diff**，只有 `write_file` / `edit_file` 走审批 diff。
- **长任务失败不等于网络不通**：前台超时会终止进程树并可能留下未写完的文件；重试前先检查已有产物（大小、源站 Content-Length/hash、上次日志），再决定是否换代理或镜像。
- `.agents/` 是点目录，`glob` / `grep` 之类的检索工具可能跳过它，看这些文件用 `read_file` 或 `run_command`。
- **`memory_write` 自己会写 frontmatter**：`name` / `description` / `alwaysApply` / `globs` 由工具生成，`content` 里再带一遍 `---` 块会让文件出现两份 frontmatter。只传正文。
- 审批模式（Manual / Edit automatically / Auto / Plan）按会话保存，Plan 模式只允许只读工具并把计划写到 `.agents/plans/`。
- MCP 是用户级配置：`%APPDATA%\Ling\mcp.json`；工具清单缓存和 OAuth 凭证在 `%LOCALAPPDATA%\ling-agent\`（`mcp-cache.json`、`mcp-auth/`）。MCP 支持 stdio、Streamable HTTP、SSE fallback、静态 headers 和 PKCE OAuth；Windows 凭证使用 DPAPI 加密。
