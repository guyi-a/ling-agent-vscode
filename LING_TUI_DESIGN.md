# Ling TUI 设计与开发说明

> 状态：阶段 0–3 已实现并通过真实终端验收；打包/发布待用户确认后执行  
> 日期：2026-11-19  
> 目标读者：在本仓库里继续开发的 Claude / 工程师  
> 核心约束：**不修改 Orca 源码**，不修改 `ling_rpc` 协议  
> 技术栈决定（2026-11-19）：**TypeScript/Node 24 + Ink 8**，npm 包名 `ling-agent`，命令 `ling`；sidecar 继续用 Python 实现，发布时以 PyInstaller 的 `ling-sidecar.exe` 随包分发。

开发前先读 `AGENTS.md` 和本文档。阶段 0–3 已完成（真实 payload 见 5.7）；`ling-cli/` 已能启动 sidecar、Markdown 对话、审批/问题、历史/队列/后台任务，并通过 Orca 终端真机验收。打包/发布待用户确认。文中仍保留的 Python/Rich/prompt_toolkit 段落是历史方案，不要再照它实现。

---

## 1. 结论与范围

### 1.1 我们要做什么

给 Ling 增加一个独立的终端前端：

```text
Orca 终端（或 Windows Terminal / VS Code 终端）
  └─ ling（npm 包 ling-agent，Node/TypeScript + Ink）
       └─ NDJSON JSON-RPC over stdio
            └─ ling-sidecar.exe（PyInstaller）或 python -m ling_rpc
                 └─ ling_agent / ling_core / ling_ai
```

用户安装后在工作区目录执行 `ling`，就能在终端里和 Ling 对话、看工具调用、处理审批、回答 `ask_user` 问题、切换审批模式、查看/恢复历史会话。开发期直接跑源码，发布时 `npm i -g ling-agent` 会同时带上 sidecar 二进制，用户不需要安装 Python。

### 1.2 不做什么

- **不修改 Orca 源码**，不新增 Orca 原生 agent、不改 Orca 的 agent picker、不改 ACP / native adapter。
- 不重写 Ling 的 agent loop、工具、审批策略。
- 不修改 `ling_rpc` 的协议版本；TUI 是现有 JSON-RPC 协议的另一个客户端。
- 不在 C: 上安装依赖或写临时文件。依赖、venv、调试产物都放 E:。
- 不把 API key 打印到终端、日志或事件记录里。

### 1.3 为什么这样更合理

在“不想维护 Orca fork”的前提下，TUI 是成本最低、边界最清晰的方案：

- Orca 终端本来就能运行任意 CLI；Ling 不需要被 Orca 识别成“Agent”也能正常工作。
- 复用 Ling 已有的 sidecar、session、审批、问题、历史、MCP、skills、computer use。
- Ling 可以脱离 Orca 使用，在 Windows Terminal、VS Code 终端、SSH 里都能跑。
- Orca 升级不会破坏 Ling；Ling 升级也不需要重新适配 Orca。
- 不需要处理 Orca 的 `TuiAgent` 联合类型、agent-session 持久化、ACP 注册、启动参数注入等内部逻辑。

代价：

- Orca 不会把 Ling 显示在 agent picker 里，也不会有 Orca 原生的工具卡片/审批卡片/状态栏。
- 审批和问题都在 Ling TUI 内部完成，交互是键盘驱动。
- TUI 是 Ling 的第三个前端（VS Code webview、headless eval、TUI），需要单独维护。
- 终端渲染有天然限制，不做图片内联、不做复杂的鼠标交互。

如果以后希望 Ling 出现在 Orca 的 agent 列表里，再单独评估 Orca 原生 adapter；TUI 仍然可以保留，不会白做。

---

## 2. 已确认的事实

这些是设计的基础，开发前不要重新猜：

### 2.1 Ling 现有架构

- `E:\毕业论文\ling-vscode\`：VS Code 扩展 + React webview。
- `E:\毕业论文\ling\`：Python harness。
- 扩展通过 stdio NDJSON JSON-RPC 启动 sidecar。
- sidecar 协议版本：`1`；server 名：`ling-sidecar`；server 版本：`0.1.0`。
- `ling_rpc/server.py` 已实现：`initialize`、`ping`、`shutdown`、`session.*`、`changes.*`、`mcp.*`、`background.*`、`tool.background`、`approval.resolve`、`question.answer`、`memory.*`、`comment.generate`、`index.*`。
- `ling_eval/rpc.py` 里有一个可参考的 Python NDJSON 客户端（`SidecarClient`），但它是 eval 侧代码；TUI 运行时代码不要直接依赖 `ling_eval`，建议新写一个精简的生产客户端。
- `ling_eval/headless.py` 里的 `_terminal_run()` / `_assistant_text()` 只用于评估，不要作为 TUI 的运行时依赖。

### 2.2 已经跑通的探针

2026-11-19 在隔离的 `ling/build/tui-probe/`（临时 `LING_STORAGE_ROOT` + 临时工作区）用真实 sidecar 跑通了阶段 0：

- `initialize`、`ping` 正常，协议 1；`capabilities.computerUse` 在禁用时为 `false`。
- `session.start` 被接受，run 从 `running` 到 `idle`；观察到 `message.delta`、`thinking.delta`、`thinking.completed`、`session.snapshot`、`session.queue`。
- Manual 模式下 `write_file` 触发 `pendingApprovals`；`approval.resolve` 允许后文件生成、run 完成；拒绝（带 `feedback`）后文件未生成，工具状态为 `Failed`。
- `run_command` 同样触发审批；允许后命令执行，工具 detail 返回 `exit_code: 0` 和 stdout。
- `ask_user` 触发 `question.requested`；`question.answer` 返回 `{"resolved": true}`，run 继续并完成。
- 重启 sidecar 后 `session.list` 能读回同一工作区的会话，`session.open` 返回 19 条 entries、`readOnly: false`；换工作区查不到。
- 探针产物在 `ling/build/tui-probe/`（gitignored）：`events.ndjson`、`approvals.ndjson`、`questions.ndjson`、`summary.json`。

2026-11-19 还在 Orca 终端用真实 sidecar 做了 TUI 真机验收：Markdown 标题/粗体/列表、`write_file` 审批与 diff、`/last`、`/history` 选择恢复、两题（单选 + 多选）`ask_user`、运行中内联队列、Esc 中断并 promote 顶部（最新）消息、后台任务 `/tasks` + `/stop-task`、中文输入输出都正常。`<Static>` 解决了历史消息在 resize 时的重复，但**底部动态区域（状态/输入框）在 Orca 连续拖拽宽度时仍会叠帧**；这是 Ink inline 渲染与终端 reflow 的同步问题，用户决定暂缓，未在 TUI 侧重写。开发期终端的 sidecar 使用 Python 源码；`ling-vscode/resources/sidecar/ling-sidecar.exe` 是旧构建，遇到“工具未返回结果”的历史会 HTTP 400，**发布前必须从当前源码重建 sidecar exe**，不要直接复制旧 exe。

仍未覆盖：computer use 在 TUI 下的表现、审批超时/过期、图片附件的真模型调用、多行输入、跨平台 sidecar 包。`remember: true`、setMode、队列、后台任务、多选/多问题 ask_user 已通过单元测试和真实终端验证。

### 2.3 配置与状态位置

- Ling 配置：`%APPDATA%\Ling\config.json`（Windows）。
- Ling 历史数据库：`%LOCALAPPDATA%\ling-agent\sessions.db`（通过 `default_state_root()`）。
- Ling 全局记忆：`%APPDATA%\Ling\memory\*.md`。
- MCP 配置：`%APPDATA%\Ling\mcp.json`。
- 工作区运行时状态：`<workspace>\.agents\`（记忆、计划、附件、后台日志等）。

TUI 默认复用这些位置，**不要**默认覆盖 `LING_STORAGE_ROOT`，否则会和 VS Code 里的历史记录分叉。

### 2.4 C: 写入边界

TUI 本身不往 C: 安装依赖，也不把临时文件写到 C:；但它默认复用 Ling 已有的历史数据库，因此 **TUI 产生的会话历史默认会写入 C:** 的 `%LOCALAPPDATA%\ling-agent\sessions.db`。

处理原则：

- 默认保持与 VS Code 共享历史，不迁移、不 junction。
- 如果 C: 空间紧张，可以显式使用 `--storage-root E:\ling-cli-state` 或设置 `LING_STORAGE_ROOT`，让 TUI 使用独立的历史库；代价是 TUI 和 VS Code 看不到彼此的新会话。
- 不要为了省 C: 空间把 `%LOCALAPPDATA%\ling-agent` 做成 junction 或直接搬走，除非先确认 Ling/VS Code 都能接受；这属于用户数据迁移，不是 TUI 的开发范围。
- TUI 的调试日志、临时工作区、venv 一律放 E:。

### 2.5 当前环境

- Node.js 24.18、npm 11.16 可用；TUI 要求 Node >= 22。
- TUI 依赖：`ink 8.0.0`、`react 19.3`、`marked 18`、`diff 9`、`typescript 5.9`；测试用 `ink-testing-library 4`。
- Python 3.14.6 仍用于从源码启动/构建 sidecar；发布包里由 PyInstaller 自带运行时。
- Ling 现有 Python 依赖在 `ling/requirements.txt`：`httpx`、`mcp`、`pypdf`、`websockets`、Windows 下的 `comtypes` / `Pillow`。
- `ling-vscode/resources/sidecar/ling-sidecar.exe` 约 48 MB；npm 包按平台分发这个 exe，用户不需要 Python。

---

## 3. TUI 产品设计

### 3.1 启动方式

安装后：

```powershell
npm install -g ling-agent
cd E:\some-workspace
ling
```

开发时：

```powershell
cd E:\毕业论文\ling-cli
npm install
npm run build
node bin/ling.js --workspace E:\some-workspace
# 或 npm run dev -- --workspace E:\some-workspace
```

建议支持：

```text
--workspace PATH        工作区，默认当前目录
--resume / --continue   打开该工作区最近一次会话；默认新建
--mode MODE             manual | accept-write | auto | plan，默认 manual
--model NAME            覆盖 config.json 的 model
--effort LEVEL          low | medium | high | xhigh | max，默认读 config
--api PROTOCOL          openai-completions | anthropic-messages
--base-url URL          覆盖 config.json 的 baseUrl
--storage-root PATH     仅调试用；默认不设置，复用 Ling 默认状态目录
--debug-log PATH        调试日志（NDJSON），不得包含 API key
--python PATH           指定 Python 解释器（仅源码 sidecar）
--check                 只握手并打印 snapshot，不进入 TUI
--no-color              关闭颜色
```

环境变量只用于开发/打包定位，不作为配置覆盖：

- `LING_SIDECAR_PATH`：指定已打包的 sidecar 可执行文件。
- `LING_PYTHON_PATH`：指定启动源码 sidecar 的 Python。
- 模型/Key/Base URL 一律从 `%APPDATA%\Ling\config.json` 和 CLI 参数读取；CLI 会清掉 shell 里的 `LING_*` / `DEEPSEEK_*` / `OPENAI_*` 旧值，避免它们串进 sidecar。

### 3.2 界面形态

**MVP 使用 inline 模式，不使用全屏 alternate screen。**

原因：

- Orca 终端基于 xterm.js，保留终端 scrollback 更利于复制、搜索、回看。
- 审批/问题用临时接管输入区的方式呈现，不需要全屏。
- 全屏 TUI 会吞掉终端滚动，且和 Orca 的终端快捷键更容易冲突。

建议布局：

```text
Ling  ·  E:\some-workspace  ·  deepseek-flash  ·  manual  ·  idle

帮我看一下这个项目的入口
Ling 我先看一下目录结构。
  ├─ Read   src/main.ts
  ├─ Read   package.json
  ╰─ Run    npm test
     Completed · 8.2s
Ling 入口是 src/main.ts，...

┌─ message ───────────────────────────────────────────────┐
│ > _                                                     │
└──────────────────────────────────────────────────────────┘
Enter 发送 · Shift+Tab 切换模式 · Ctrl+C 取消 · /help · 1/2/3 审批
```

用户消息用 `User:` 标签（绿色），assistant 用 `Ling:` 标签（青色），两个标签等宽，所以内容从同一列开始；assistant 的后续 Markdown 行与首行内容对齐。同一 `runId` 内只要有工具调用，`Ling:` 就作为整个回合的标题单独占一行，工具卡片和回答都缩进在它下面；纯文本回合仍保持 `Ling: 回答` 同行。状态信息（mode/workspace/model/phase）放在输入框内部，输入框边框颜色跟随审批模式。输入 `/` 时在输入框上方弹出命令菜单，支持 ↑/↓ 选择、Tab 补全、Enter 执行、Esc 关闭。原生终端光标已隐藏，界面内用 `▌` 显示输入位置。

实现建议：

- **Ink 8 + React 19** 负责终端渲染：`<Box>` / `<Text>` 组布局，`useInput` 处理键盘，`useApp().exit()` 退出；不使用 alternate screen。
- 已完成的 message/tool/approval/compaction 通过 Ink `<Static>` 写入 scrollback，只渲染一次；动态区域只保留当前 run 的流式消息、running/waiting 工具、队列、弹层和输入框。
- 曾尝试在 `src/tui/index.tsx` 里先于 Ink 注册 resize listener 并调用 `instance.clear()`，但在 Orca 连续拖拽时仍会与 Ink 自身的 resize 处理抢时序，无法稳定消除叠帧；该实验已撤回。当前已知问题见 2.2 / 9.5，暂不处理。
- `message.delta` 按 `messageId` 累积流式文本；`session.snapshot` 到达时以 snapshot 为准。不要用 delta 自己拼完整历史。
- thinking 默认只显示一行 `Thinking…`；`Ctrl+Alt+T` 展开/收起最近一轮，避免刷屏。
- 工具卡片默认一行：`Read src/main.ts`、`Edit src/app.ts +12 −3`、`Run npm test`；失败时红色并显示错误摘要。
- 工具失败时自动展开错误；成功时保持折叠，后续用 `/details [n]`（1 = 最近一次）或 `/last` 查看完整内容。
- Markdown 已用 `marked` 的 token 流渲染（标题、粗体、斜体、行内代码、代码块、列表、引用、链接、表格、删除线），由 `src/tui/markdown.tsx` 负责；流式时每次 delta 重新解析。

### 3.3 输入与快捷键

- `Enter`：发送；输入 `/` 打开命令菜单时执行选中的命令。
- `Shift+Tab`：按 `manual → accept-write → auto → plan` 循环切换审批模式；运行中会提示先取消。
- `Ctrl+Alt+T`：展开/收起 thinking 块。只对最近一轮生效：最后一条 assistant 消息会暂缓进入 `<Static>`，被下一条用户消息顶掉时才写入 scrollback。
- `Ctrl+C`：有运行中的 run 时先取消 run；空闲时退出。
- `Ctrl+D`：输入为空时退出。
- `Esc`：优先关闭 overlay/命令菜单/自定义输入；否则中断当前 run。队列非空时中断并 promote 顶部（最新）消息，队列为空时直接取消。空闲时不退出程序。
- 输入 `/`：显示命令菜单；`↑`/`↓` 选择，`Tab` 补全命令，`Enter` 执行，`Esc` 关闭。
- `Alt+Enter` 或 `Ctrl+J` 多行输入、历史输入、`Ctrl+L` 清屏、`/attach` 路径补全尚未实现，属于后续打磨。

不要在 TUI 里占用 Orca 可能拦截的快捷键；所有重要功能都要有 `/命令` 兜底。

### 3.4 会话区

支持以下 entry（来自 `session.snapshot`）：

- `message`：user / assistant。
- `tool`：工具调用卡片。
- `approval`：已解决的审批结果。
- `task-notification`：后台任务完成通知。
- `compaction`：上下文压缩提示。

渲染要点：

- user 消息显示纯文本（绿色），不再显示 `You` 标签。
- 纯文本回合：assistant 的 `Ling` 标签与回答首行同行，后续行与回答内容左对齐；支持 Markdown。
- 含工具的回合：按 `runId` 归组，`Ling:` 单独一行作为回合标题，工具卡片和 assistant 文本缩进两格；一个回合只出现一次 `Ling:`。
- assistant 的 `thinking` 默认只显示 `Thinking…`；`Ctrl+Alt+T` 展开（只对最近一轮生效）。展开内容汇总该 run 内所有 assistant message 的 `thinking.delta` / `message.thinking`，以 dim italic 显示在 Ling 回合内、回答上方，避免只看到最后一段 reasoning；纯文本回合展开时 `Ling:` 也改为回合标题，思考块和回答都缩进在它下面。
- 已完成的 message/tool/approval/compaction 进入 `<Static>`，只写一次；当前 run 的流式消息和 running/waiting 工具留在动态区域。
- tool 的 `target`、`action`、`status` 要清晰；call id 不显示在卡片上。
- tool 的 `detail.kind == "diff"` 时，显示 `+added −removed`，并支持 `/details` 打开 overlay 查看 diff。
- `detail.kind == "io"` 时，失败显示一条去重后的错误摘要；详情通过 `/details` 查看。
- `approval` entry 显示“已允许/已拒绝”以及目标。
- `compaction` entry 显示“上下文已压缩，替换 N 条消息”。

### 3.5 工具卡片

`session.snapshot` 里的工具 entry 形状大致是：

```json
{
  "kind": "tool",
  "runId": "run-...",
  "tool": {
    "id": "tool-...",
    "name": "edit_file",
    "action": "Edit",
    "target": "src/app.ts",
    "status": "Running",
    "details": ["Tool: edit_file", "Arguments: {...}"],
    "detail": {
      "kind": "diff",
      "path": "src/app.ts",
      "rows": [],
      "added": 12,
      "removed": 3,
      "truncated": false
    }
  }
}
```

`status` 的取值：`Waiting`、`Running`、`Completed`、`Failed`、`Cancelled`。  
`action` 的取值：`Read`、`Edit`、`Run`、`Skill`、`Plan`、`Todo`、`Subagent`、`Browser`、`Computer`、`Web`、`Memory`、`MCP`。

MVP 渲染建议：

```text
  ├─ Read   src/app.ts
  ├─ Edit   src/app.ts  +12 −3
  ├─ Run    npm test
  │    Completed · exit 0
  ╰─ MCP    chatcut_desktop
```

当前实现：

- 工具卡片默认一行，颜色区分：Skill 品红、Read/Web/Browser 蓝、Edit/Write 黄、Run 青、MCP 绿、Computer 红、Plan/Todo 青；target 加粗；Completed 绿、Running/Waiting 黄、Failed/Cancelled 红。卡片不显示 call id（`/last` 仍可展开最近一次工具详情）。
- 含工具调用的回合按 `runId` 归组：`Ling:` 单独占一行，工具卡片和 assistant 文本缩进两格；纯文本回合仍与 `Ling:` 同行。
- `detail.kind == "diff"` 显示 `+added −removed`，失败时显示一条去重后的错误摘要。
- `/details [n]`（1 = 最近一次）和 `/last` 在 overlay 中显示详情；diff 逐行显示 `+`/`-`/context，超过 200 行标记截断；io 显示 input/output，超过 40 行标记截断。
- `/details [n]` 按调用倒序取第 n 条；也仍接受 tool id 或前缀（用于脚本/调试）。`/last` 找最后一条带 id 的 tool entry。
- 已完成的工具 entry 进入 `<Static>`，不再在对话里原地展开；详情统一走 overlay。

### 3.6 审批卡片

当 `snapshot.phase == "waiting_approval"` 或 `pendingApprovals` 非空时：

1. 暂停普通输入。
2. 显示第一个待审批请求。
3. 用户按键后调用 `approval.resolve`。
4. 若还有下一个，继续显示；否则恢复输入。

建议卡片：

```text
Approval required · edit_file
Target: src/app.ts
Reason: Review the proposed change before applying it.

  --- a/src/app.ts
  +++ b/src/app.ts
  @@ ...
  -old line
  +new line

[1] Allow once
[2] Yes, allow all edits this session
[3] Deny
[4] Deny with feedback...
```

按键语义：

- `1`：`approval.resolve`，`approved: true`。
- `2`：仅当请求里有 `allowAlways` 时出现：
  - `allowAlways.kind == "mode"`：先 `session.setMode` 切换模式（例如 `accept-write`），再 `approval.resolve` 允许一次。
  - `allowAlways.kind == "command"`：`approval.resolve` 带 `remember: true`。
  - 没有 `allowAlways` 就不显示 `2`。
- `3`：`approved: false`。
- `4`：进入反馈输入，然后 `approved: false, feedback: "..."`。
- `Esc`：关闭卡片，不自动批准；可以提示“使用 1/3 处理审批，或 Ctrl+C 取消运行”。

**重要：** 服务端的 `approval.resolve` 只支持 `approved: bool`，没有“永久允许写文件”的独立 decision。写文件的“以后都允许”是通过先切到 `accept-write` 模式实现的，和 VS Code webview 的行为保持一致。

待审批请求的关键字段：

```json
{
  "id": "approval-...",
  "tool": "edit_file",
  "action": "Edit",
  "target": "src/app.ts",
  "reason": "Review the proposed change before applying it.",
  "arguments": {},
  "effectDigest": "...",
  "details": [],
  "allowAlways": {
    "kind": "mode",
    "mode": "accept-write",
    "label": "Yes, allow all edits this session"
  }
}
```

`approval.resolve` 参数必须原样带 `id` 和 `effectDigest`：

```json
{
  "approvalId": "approval-...",
  "effectDigest": "...",
  "approved": true,
  "remember": false,
  "feedback": null
}
```

注意字段名是 `approvalId`，而待审批对象里的字段名是 `id`。

审批卡片通过 `src/tui/detail.tsx` 展示详情：

- `run_command`：显示完整命令，最多 20 行。
- `write_file`：把 `arguments.content` 渲染成新增 diff。
- `edit_file`：用 `diff` 包的 `diffLines(old_text, new_text)` 生成逐行 diff，最多 80 行。
- 其他工具：显示 arguments JSON，最多 20 行。
- 超出限制时明确显示 `[... truncated ...]`，不要伪装成完整内容。
- `[1]` 允许一次；`[2]` 仅在 `allowAlways` 存在时出现：`kind == "mode"` 先 `session.setMode` 再 resolve，`kind == "command"` resolve 时带 `remember: true`；`[3]` 拒绝；`[4]` 输入反馈后拒绝。

### 3.7 ask_user 问题卡片

`question.requested` 事件形状：

```json
{
  "type": "question.requested",
  "question": {
    "callId": "ask-1",
    "questions": [
      {
        "id": "q1",
        "question": "PDF 风格？",
        "header": "Style",
        "options": [
          { "label": "Fast", "description": "更快" },
          { "label": "Pretty", "description": "更精美" }
        ],
        "multiSelect": false
      }
    ]
  }
}
```

回答：

```json
{
  "callId": "ask-1",
  "answers": [
    { "id": "q1", "selected": ["Pretty"], "custom": null }
  ]
}
```

取消：

```json
{
  "callId": "ask-1",
  "cancelled": true
}
```

UI 要点（已实现）：

- 单选：按数字选择并立即进入下一题；多选：按数字切换 `[x]`，Enter 进入下一题。
- `c` 进入自定义回答；Enter 提交自定义文本；对单选会用 custom 替代 selected，对多选会保留已选标签并附加 custom。
- 没有 options 时直接输入文本，Enter 作为 custom 提交。
- 支持多个问题（最多 4 个），逐题回答，最后一题提交时一次性发送全部 answers；未回答的题以空 selected 提交（协议允许）。
- Esc 在自定义输入中先退出输入；否则中断当前 run（队列非空时会 promote 顶部（最新）消息），不自动替用户选默认值。
- `question.resolved` 到达后清理草稿；snapshot 的 `pendingQuestions` 仍是兜底。

### 3.8 状态栏

状态信息放在输入框内部（输入框第一行），不单独占对话区：

- approval mode：`manual` / `accept-write` / `auto` / `plan`，用颜色区分（manual 蓝、accept-write 黄、auto 绿、plan 品红）。
- workspace 名称或路径末尾。
- model。
- phase：`idle` / `running` / `waiting_approval` / `cancelled` / `failed`。
- queued message 数量。
- background task 数量。
- pending approval / question 数量。

输入框边框和标签颜色跟随 approval mode；命令菜单里命令名用青色/加粗、说明用灰色，选中项为青底黑字。

状态来源优先级：

1. 最近的 `session.snapshot`。
2. `session.queue`、`background.updated`、`question.*` 等增量事件。
3. 本地启动参数（仅作为初始值）。

### 3.9 斜杠命令

已实现：

- `/help`：显示命令和快捷键。
- `/new`：`session.new`，清空当前会话视图，mode 回到 manual。
- `/mode`：无参数时显示当前模式并提示；带参数调用 `session.setMode`。
  - `/mode manual`
  - `/mode accept-write`
  - `/mode auto`
  - `/mode plan`
- `/effort`：设置下一条消息的 `reasoning`；不写 config，只影响本 TUI 进程。
  - `/effort low|medium|high|xhigh|max`
- `/history [query]`：`session.list` 或 `session.search`，在 overlay 里显示最近会话；`↑/↓` 选择，`Enter` 通过 `session.open` 恢复并继续，`Esc` 关闭。
- `/tasks`：`background.list`，显示后台任务状态/退出码/错误。
- `/stop-task <id>`：`background.stop`。
- `/details [n]`：在 overlay 里显示某个工具 entry 的 diff/io（1 = 最近一次）。
- `/last`：在 overlay 里显示最后一条工具 entry 的详情。
- `/attach <path>`：把文件或图片附到下一条消息；图片复制到 `.agents/attachments/`，普通文件直接引用绝对路径。
- `/detach`：清空待发送附件。
- `/mcp`：`mcp.status`，显示配置文件、服务器状态、工具数量、授权和错误。
- `/index`：`index.build`，overlay 显示构建进度（监听 `index.progress`）和最终统计。
- `/index-status`：`index.status`，显示 files/totalFiles/percent、chunks、vectors、model。
- `/changes [runId]`：`changes.get`，默认取最近一个带 `runId` 的 entry，显示文件路径和 `+added −removed`。
- `/status`：重新拉取 `session.snapshot` 并显示 queue/background 数量。
- `/quit` 或 `/exit`：关闭。

队列不通过斜杠命令查看：运行中发送的消息会自动显示在对话下方的 `Queued · N` 区域，最新的消息在上面。按 `Esc`：

- 队列非空：中断当前 run，并调用 `session.queued.sendNow` 把顶部（最新）消息插队执行；
- 队列为空：调用 `session.cancel` 中断当前 run。
- 审批/问题弹层中按 Esc 同样中断当前 run（不自动回答）。
- 没有运行中的 run 时，Esc 只关闭 overlay/命令菜单或提示。

未实现（后续）：

- `/clear`：只清屏。
- `/revert`：`changes.revert` 回滚本轮修改。
- `/delete <sessionId>`：`session.delete`（需要确认）。
- `/regenerate <messageId>`：`session.regenerate`。

### 3.10 历史会话

启动默认新建会话；`--resume` 时：

1. `session.list`，带 `workspaceRoot`。
2. 取最近一条。
3. `session.open`，带上 `sessionId` 和 `workspaceRoot`。
4. 用返回的 snapshot 渲染。

`session.list` 只返回当前工作区、最多 50 条。  
`session.open` 会恢复会话；之后 `session.start` 继续同一会话。  
`session.history` 是只读的历史快照，不适合“继续聊”；继续聊要用 `session.open`。

历史打开后：

- `snapshot.readOnly == false` 才允许继续发送。
- 如果 `readOnly == true`，显示提示并禁用输入，或引导使用 `/history` 打开可写会话。

---

## 4. TUI 进程架构

### 4.1 进程模型

```text
ling（Node/TypeScript TUI 主进程）
  ├─ Node event loop + Ink reconciler
  ├─ useInput / React state
  └─ child_process.spawn（子进程）
        ├─ ling-sidecar.exe（发布包自带，内含 Python 运行时）
        └─ python -m ling_rpc（开发期源码）
```

不要直接在 TUI 进程里 import `SessionService` 并调用。原因：

- 复用 VS Code 使用的同一套 RPC 行为。
- 审批、问题、队列、后台任务的事件语义不需要重新实现。
- sidecar 崩溃不会拖垮 TUI。
- 发布时可以替换成打包后的 exe，用户不需要 Python。

### 4.2 模块划分

实际布局：

```text
E:\毕业论文\ling-cli\
  package.json                 # name=ling-agent, bin.ling=bin/ling.js
  tsconfig.json                # ESM / NodeNext / react-jsx
  bin\ling.js                  # #!/usr/bin/env node，import ../dist/index.js
  src\
    index.ts                   # CLI 参数、--check、启动 TUI
    config.ts                  # 读 config.json、CLI 覆盖、sidecar env、exe/python 解析
    rpc.ts                     # NDJSON JSON-RPC 子进程客户端
    types.ts                   # snapshot / event / approval / question 类型
    tui\
      index.tsx                # 启动 sidecar、initialize、render(<App/>)
      App.tsx                  # Ink 主界面、输入、状态机、审批/问题/历史/队列
      markdown.tsx             # marked token -> Ink
      detail.tsx               # tool diff/io、审批命令/内容/diff
    tests\
      app.test.tsx             # Ink/交互单元测试
      config.test.ts           # 配置/env/sidecar 解析测试
      rpc.test.ts              # RPC 超时/通知/退出测试
  scripts\
    tui-smoke.mjs              # 真实 sidecar 端到端冒烟
    copy-sidecar.mjs           # 打包前复制 ling-sidecar.exe
    copy-skills.mjs            # 打包前复制 .agents/skills
    clean-dist.mjs             # 构建/打包前清理
    clean-dist-tests.mjs       # 测试构建前清理
  vendor\win32-x64\
    ling-sidecar.exe           # npm 打包产物，gitignored
```

CI：`.github/workflows/ling-cli.yml`，Windows + Node 24，`npm ci` + `npm test`。

测试：

```text
E:\毕业论文\ling-cli\src\tests\
  app.test.tsx                 # 渲染、提交、审批、问题、流式 delta
```

遵循仓库现有约定：

- TypeScript 使用 prettier 风格（双引号、行尾逗号、约 80 列），无 lint 步骤，靠 `npm run build`（tsc）把关。
- ESM + NodeNext；相对 import 必须带 `.js` 后缀；JSX 用 `react-jsx`。
- 代码注释用英文；面向用户的提示可以用中文。
- 测试用 `node:test` + `ink-testing-library`，通过 `npm test` 跑；不要引入 vitest/jest。

### 4.3 RPC 客户端

`src/rpc.ts` 职责：

- `child_process.spawn` 启动 sidecar，参数数组、`stdio: ["pipe","pipe","pipe"]`，不使用 shell。
- stdout/stdin 用 NDJSON；逐行 `JSON.parse`，只接受 `jsonrpc == "2.0"`。
- 响应按数字 `id` 匹配；`ui.event` → `params.event`，`index.progress` → `params`。
- `start()` 等待子进程 `spawn` 事件；启动失败清晰报错。
- `close()` 先发 `shutdown`，等待退出；超时 terminate，再 kill。
- 子进程退出时，所有 pending request 以错误完成，TUI 显示 sidecar 退出信息。
- `stderr` 只在内存保留尾部，失败时输出；debug log 只记录方法名、事件类型、耗时，不写参数/Key。

关键细节：

- Node `readline` 没有 Python 的 8MB 行限制；超大工具输出仍要在 UI 截断。
- 写入用 `JSON.stringify`（天然 UTF-8），行尾 `\n`。
- 请求超时默认 30 秒；`initialize` 20 秒；`session.start` 只等 accepted，不等整个 run。
- `session.cancel` 可能等待 run 结束；给它较长超时（例如 60 秒）。
- `stderr` 不要直接打到对话区；只在错误/调试日志里显示。
- API key 用 `redact` 函数在错误文本中替换成 `<redacted>`。

### 4.4 状态管理

`src/tui/App.tsx` 用 React state 维护 UI 状态：

```text
entries            TimelineEntry[]        snapshot 的 entries
staticEntries      TimelineEntry[]        已完成的 entry，写入 <Static>
dynamicEntries     TimelineEntry[]        当前 run 的流式/running/waiting entry
phase              string                 idle/running/waiting_approval/...
mode               string                 approvalMode
pendingApprovals   ApprovalRequest[]      snapshot 的 pendingApprovals
pendingQuestions   QuestionRequest[]      snapshot 的 pendingQuestions
queuedMessages     QueuedMessage[]        内联显示在对话下方（最新在上）
backgroundTasks    BackgroundTask[]       后台任务
error              string | null          snapshot.error / run.failed
deltas             Map<messageId, text>   流式 assistant 文本
thinking           Map<messageId, text>   流式 thinking（默认只显示一行）
input              输入框内容
notice             本地提示（命令、错误摘要）
effort             本条消息的 reasoning；不写 config
feedback/feedbackMode  拒绝审批时的反馈输入
```

处理规则：

- **`session.snapshot` 是权威状态**：整体替换 `entries`、`pendingApprovals`、`pendingQuestions`、`queuedMessages`、`backgroundTasks`、`phase`、`approvalMode`、`error`、`readOnly`。
- `message.delta` 只用于流式显示：按 `messageId` 追加到 `deltas`；如果 snapshot 随后到达，以 snapshot 的 `message.content` 为准。
- `thinking.delta` 同理；`thinking.completed` 更新完成标记。
- `session.queue` 更新 `queuedMessages`；UI 反转顺序显示（新消息在上）。
- `background.updated` 更新/插入对应 task。
- 新的最终 entry（user message、完成/失败的 tool、approval、compaction、最终 assistant 文本）追加到 `staticEntries`；同一个 id 只写一次。会话切换（`/new`、`/history` 选择、sessionId 变化）时清空 static/emitted。
- Esc：overlay/菜单/自定义输入优先关闭；否则中断当前 run。队列非空时 `session.queued.sendNow` promote 顶部（最新），队列为空时 `session.cancel`。
- `question.requested` / `question.resolved` 更新 pending questions；snapshot 也包含 pendingQuestions，最终以 snapshot 为准。
- `run.failed` / `run.cancelled` 更新错误/状态提示，并等待下一次 snapshot。
- `plan.created` 显示计划文件路径，不自动打开（终端里没有编辑器）。
- 不要根据 delta 自己构造完整会话历史；历史以 snapshot 为准。
- 当前实现每 1.5 秒额外拉一次 `session.snapshot` 作为兜底；不能依赖所有增量事件都到达。

### 4.5 输入与运行并发

运行中允许继续输入：

- 提交时调用 `session.start`；如果当前有 run，sidecar 会入队并返回：

```json
{ "accepted": true, "queued": true, "queueLength": 1 }
```

- TUI 显示“已加入队列”，不把这条消息直接追加到已提交消息区；等 snapshot 里出现 user entry 后再显示。
- 如果没有运行，`session.start` 返回 `{"accepted": true}`。
- 运行中发送的消息自动进入队列并在对话下方显示；按 Esc 中断当前 run，并 promote 顶部（最新）消息（`session.queued.sendNow`）。队列为空时 Esc 调用 `session.cancel`。

审批/问题到达时：

- 暂停普通输入，进入 modal 状态。
- 处理完所有 pending 后恢复。
- 运行中仍允许 Esc 中断、`/status` 等安全操作。

### 4.6 sidecar 生命周期

启动顺序：

1. 解析 workspace。
2. 读 config。
3. 构建环境变量。
4. 启动 sidecar（源码 cwd 为 `E:\毕业论文\ling`；发布包为 exe 所在目录）。
5. 发送 `initialize`，校验 `protocolVersion == 1`。
6. 可选：`ping`。
7. 拉取 `session.snapshot`；如果 `--resume`，先 `session.list` + `session.open`。
8. 渲染初始状态。

退出顺序：

1. 停止输入。
2. 发送 `shutdown`（短超时）。
3. 等待进程退出；超时 terminate；再超时 kill。
4. 恢复终端状态（Ink 实例 `unmount()`；不要留下 raw mode / 光标隐藏）。
5. 打印退出提示（可选）。

异常：

- sidecar 启动失败：显示 stderr tail 和启动命令（不含 key），退出。
- 运行中 sidecar 退出：显示错误，不自动无限重启；提示用户重新启动 TUI。
- 工具/模型错误：显示 `run.failed` 的消息，保留会话。

### 4.7 配置读取与 sidecar 环境

`src/config.ts` 负责：

- 读取 `%APPDATA%\Ling\config.json`。
- 应用 CLI 覆盖。
- 校验 API key 是否存在；缺失时给出清晰提示，不打印 key。
- 构建 sidecar 环境变量。
- 解析 sidecar 启动方式：`LING_SIDECAR_PATH` > `vendor/<platform>-<arch>/ling-sidecar.exe` > 源码 `python -m ling_rpc`。

建议复用的 config 字段：

```json
{
  "version": 1,
  "apiKey": "...",
  "model": "deepseek-flash",
  "baseUrl": "https://api.deepseek.com/v1",
  "api": "openai-completions",
  "effort": "high",
  "maxSteps": 50,
  "maxParallelTools": 4,
  "commandTimeoutSeconds": 3600,
  "approvalTimeoutSeconds": 120,
  "contextWindowTokens": 1000000,
  "keepLastUserTurns": 5,
  "embeddingModel": "text-embedding-v4",
  "embeddingBaseUrl": "...",
  "embeddingApiKey": "",
  "computerUseEnabled": true,
  "computerSessionTtlSeconds": 600,
  "computerSessionIdleSeconds": 90,
  "computerDeniedApps": "",
  "computerAuditEnabled": true
}
```

构建 sidecar env 的原则：

- 复制当前进程环境（保留代理、PATH 等）。
- 删除已知的 Ling/模型环境变量，避免 shell 里的旧值覆盖 config：
  - `LING_API_KEY`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`
  - `LING_MODEL`、`LING_API_BASE_URL`、`LING_API_PROTOCOL`、`LING_PROVIDER`
  - `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL`
  - `LING_SKILLS_ROOT`、`LING_MAX_STEPS`、`LING_MAX_PARALLEL_TOOLS`
  - `LING_COMMAND_TIMEOUT_SECONDS`、`LING_APPROVAL_TIMEOUT_SECONDS`
  - `LING_CONTEXT_WINDOW_TOKENS`、`LING_KEEP_LAST_USER_TURNS`
  - `LING_BROWSER_BRIDGE_PORT`
  - `LING_EMBEDDING_MODEL`、`LING_EMBEDDING_BASE_URL`、`LING_EMBEDDING_API_KEY`
  - `LING_COMPUTER_*`
- 设置：
  - `LING_API_KEY`
  - `LING_MODEL`
  - `LING_API_BASE_URL`
  - `LING_API_PROTOCOL`
  - `LING_MAX_STEPS`
  - `LING_MAX_PARALLEL_TOOLS`
  - `LING_COMMAND_TIMEOUT_SECONDS`
  - `LING_APPROVAL_TIMEOUT_SECONDS`
  - `LING_CONTEXT_WINDOW_TOKENS`
  - `LING_KEEP_LAST_USER_TURNS`
  - embedding 三项（如果配置里有）
  - `LING_COMPUTER_ENABLED` 等
  - `LING_SKILLS_ROOT`：如果 `<repo>\.agents\skills` 存在，设为该路径；否则不设置。
  - `PYTHONIOENCODING=utf-8`
  - `PYTHONUTF8=1`
  - `PYTHONUNBUFFERED=1`
- 不要设置 `LING_STORAGE_ROOT`，除非用户显式传 `--storage-root`。
- 不要把 API key 写入调试日志；调试日志只记录方法名、事件类型、耗时、错误摘要。

sidecar 启动命令解析（`src/config.ts`）：

```ts
const explicit = process.env.LING_SIDECAR_PATH?.trim();
if (explicit) {
  return { kind: "exe", command: explicit, args: [], cwd: dirname(explicit) };
}
const vendor = join(packageRoot, "vendor", `${process.platform}-${process.arch}`,
  process.platform === "win32" ? "ling-sidecar.exe" : "ling-sidecar");
if (existsSync(vendor)) {
  return { kind: "exe", command: vendor, args: [], cwd: dirname(vendor) };
}
const python = process.env.LING_PYTHON_PATH?.trim() ?? "python";
return { kind: "python", command: python, args: ["-m", "ling_rpc"],
  cwd: join(repoRoot, "ling") };
```

启动用 `child_process.spawn(command, args, { cwd, env, stdio: ["pipe","pipe","pipe"], windowsHide: true })`，不使用 shell。

### 4.8 配置与安全边界

- TUI 只读 config，MVP 不写 config；用户要改模型/key 仍在 VS Code 的 Ling 设置里改。
- API key 绝不出现在：
  - 终端输出
  - 日志
  - `--debug-log`
  - 异常 traceback 的参数里
- 启动 sidecar 用参数数组，不用 shell。
- 审批默认 manual；不得提供“全部自动允许”的快捷开关绕过 sidecar 审批。
- 不把审批请求里的命令直接交给 shell 执行；TUI 只调用 `approval.resolve`，实际执行由 sidecar 负责。
- 工作区外路径、绝对路径等安全判断由 Ling 的 `workspace.py` / 审批策略负责，TUI 不绕过。
- 调试日志放在 E:，例如 `E:\毕业论文\ling\build\tui-debug.ndjson`；不要写 C: 临时目录。

---

## 5. RPC 契约速查

### 5.1 `initialize`

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientInfo": { "name": "ling-cli", "version": "0.1.0" }
  }
}
```

响应：

```json
{
  "protocolVersion": 1,
  "serverInfo": { "name": "ling-sidecar", "version": "0.1.0" },
  "capabilities": {
    "ping": true,
    "sessions": true,
    "comments": true,
    "computerUse": true
  }
}
```

### 5.2 `session.start`

```json
{
  "prompt": "用户输入",
  "workspaceRoot": "E:\\some-workspace",
  "approvalMode": "manual",
  "reasoning": "high",
  "attachments": [],
  "selections": [],
  "activePaths": [],
  "displayText": "可选，UI 显示文本"
}
```

- `reasoning` 可选值：`low`、`medium`、`high`、`xhigh`、`max`；`auto` / `null` 表示不指定。
- `approvalMode` 可选值：`manual`、`accept-write`、`auto`、`plan`。
- 运行中再次调用会入队，返回 `queued: true`。
- 返回 `{"accepted": true}` 或 `{"accepted": true, "queued": true, "queueLength": N}`。

### 5.3 `session.snapshot`

无参数，直接返回 snapshot 对象：

```json
{
  "phase": "idle",
  "entries": [],
  "pendingApprovals": [],
  "pendingQuestions": [],
  "queuedMessages": [],
  "backgroundTasks": [],
  "computerSession": null,
  "error": null,
  "sessionId": "...",
  "runs": [],
  "readOnly": false,
  "approvalMode": "manual"
}
```

`phase`：`idle`、`running`、`waiting_approval`、`cancelled`、`failed`。

注意：`ui.event` 里的 `session.snapshot` 是包在 `event` 里的：

```json
{
  "jsonrpc": "2.0",
  "method": "ui.event",
  "params": {
    "event": {
      "type": "session.snapshot",
      "snapshot": { "...": "..." }
    }
  }
}
```

### 5.4 其他常用方法

```text
session.cancel                  {} -> {"cancelled": true}
session.new                     {} -> {"accepted": true}
session.setMode                 {"mode": "manual"} -> {"mode": "manual"}
session.list                    {"workspaceRoot": "..."} -> {"sessions": [...]}
session.search                  {"query": "...", "workspaceRoot": "..."} -> {"sessions": [...]}
session.history                 {"sessionId": "..."} -> snapshot（只读）
session.open                    {"sessionId": "...", "workspaceRoot": "..."} -> snapshot
session.delete                  {"sessionId": "..."} -> {"deleted": true}
session.deleteMessage           {"messageId": "..."} -> ...
session.regenerate              {"messageId": "...", "reasoning": "high"} -> ...
session.queued.sendNow          {"queuedId": "..."} -> {"accepted": true}
background.list                 {"sessionId": "..."} -> {"tasks": [...]}
background.stop                 {"taskId": "..."} -> {"task": {...}}
tool.background                 {"callId": "..."} -> {"task": {...}}
mcp.status                      {"workspaceRoot": "..."} -> ...
mcp.ensureConfig                {"workspaceRoot": "..."} -> ...
approval.resolve                {"approvalId": "...", "effectDigest": "...", "approved": true, "remember": false, "feedback": null}
question.answer                 {"callId": "...", "answers": [...]} 或 {"callId": "...", "cancelled": true}
ping                            {"nonce": "..."} -> {"pong": true, "nonce": "..."}
shutdown                        {} -> null
```

### 5.5 UI 事件类型

已确认会出现的类型：

- `session.snapshot`
- `message.delta`
- `thinking.delta`
- `thinking.completed`
- `plan.created`
- `session.queue`
- `background.updated`
- `computer.session.updated`
- `question.requested`
- `question.resolved`
- `subagent.trace`
- `run.failed`
- `run.cancelled`

另有独立通知：

- `index.progress`（不是 `ui.event`，params 是进度对象）

TUI 必须对未知事件类型宽容：记录到调试日志，不要崩溃。

### 5.6 附件

`session.start` 的附件格式：

```json
{
  "relativePath": "path/in/workspace.png",
  "name": "screenshot.png",
  "mediaType": "image/png",
  "kind": "image"
}
```

- 图片：`kind: "image"`，`relativePath` 必须是工作区内相对路径，且 mediaType 以 `image/` 开头。
- 普通文件：`kind: "file"`，`relativePath` 可以是绝对路径。
- 最多 4 个。
- 已实现：普通文件直接用绝对路径；图片先校验类型和 4 MB 上限，再复制到 `.agents/attachments/`，用工作区相对路径发送。
- 终端输入只能传文本路径，不能直接接收剪贴板图片；`/attach <path>` 是当前入口，拖拽文件到终端得到路径后也可以直接附加。

### 5.7 阶段 0 实测 payload（2026-11-19）

以下为真实 sidecar 在 `manual` 模式下返回的对象。字段与前面的示例一致，但实际值和个别行为以本节为准。

`write_file` 审批（`pendingApprovals[0]`）：

```json
{
  "id": "approval-eca16872-911b-4b7a-b869-453fa8569541",
  "tool": "write_file",
  "action": "Edit",
  "target": "allow_probe.txt",
  "reason": "write E:\\...\\workspace\\allow_probe.txt",
  "arguments": {
    "path": "allow_probe.txt",
    "content": "LING_ALLOW_PROBE"
  },
  "effectDigest": "2e361a14...",
  "details": [
    "Tool: write_file",
    "Arguments: {\"content\": \"LING_ALLOW_PROBE\", \"path\": \"allow_probe.txt\"}",
    "Effect digest: 2e361a14..."
  ],
  "allowAlways": {
    "kind": "mode",
    "mode": "accept-write",
    "label": "Yes, allow all edits this session"
  }
}
```

- `target` 是相对路径；`reason` 实际是 `write <绝对路径>`，不是固定文案。
- `arguments` 的键顺序不固定（`path` / `content` 可能交换），不要按顺序解析。
- `details` 里已经带了 effect digest；主卡片不用重复展示。

`run_command` 审批：

```json
{
  "id": "approval-c2c786bd-2013-40d7-b18d-4b7c1ec34a5e",
  "tool": "run_command",
  "action": "Run",
  "target": "echo LING_COMMAND_PROBE",
  "reason": "normal command",
  "arguments": {
    "command": "echo LING_COMMAND_PROBE",
    "description": "探测命令 echo"
  },
  "effectDigest": "629f640c...",
  "details": ["Tool: run_command", "Arguments: {...}", "Effect digest: 629f640c..."],
  "allowAlways": {
    "kind": "command",
    "label": "Yes, and don't ask again for \"echo\" commands"
  }
}
```

- `target` 是完整命令行；`reason` 可能是 `normal command`。
- `allowAlways.kind == "command"`，没有 `mode`；对应 `approval.resolve` 的 `remember: true`。
- `arguments.description` 是模型生成的标签，可能是中文。

`approval.resolve` 的返回：

```json
{"resolved": true}
```

允许和拒绝都返回同样的形状（前提是 `id` + `effectDigest` 匹配且未过期）。拒绝时把 `approved` 设为 `false`，`feedback` 会进入工具错误文本。

拒绝后的时间线：

- 工具 entry 的 `status` 是 `"Failed"`，不是 `"Cancelled"`。
- `details` 里会出现 `Error: User denied the tool call: ...`，实测可能重复成 `Error: Error: ...`；TUI 渲染时去重或只显示一条即可。
- 随后出现已解决的审批 entry：

```json
{
  "kind": "approval",
  "runId": "run-...",
  "approval": {
    "id": "result-approval-eca16872-911b-4b7a-b869-453fa8569541",
    "approvalId": "approval-eca16872-911b-4b7a-b869-453fa8569541",
    "approved": false,
    "target": "deny_probe.txt",
    "action": "Edit"
  }
}
```

`ask_user` 请求（`question.requested` 的 `question`）：

```json
{
  "callId": "call_00_...",
  "questions": [
    {
      "id": "probe_color",
      "question": "Probe color?",
      "header": "Probe",
      "options": [
        {"label": "Red", "description": "Choose red"},
        {"label": "Blue", "description": "Choose blue"}
      ],
      "multiSelect": false
    }
  ]
}
```

- UI 里的 `options` 已归一化为 `{label, description}`；`multiSelect` 是 camelCase。
- 模型工具参数用的是 `multi_select`（snake_case），TUI 不直接消费工具参数，只消费 UI 事件。
- 注意 `options` 可能为空；此时 TUI 应允许自定义回答，而不是显示空列表。

`question.answer`：

```json
{
  "callId": "call_00_...",
  "answers": [
    {"id": "probe_color", "selected": ["Blue"]}
  ]
}
```

- 没有自定义回答时可以省略 `custom`；返回 `{"resolved": true}`。
- 取消时发送 `{"callId": "...", "cancelled": true}`（未在阶段 0 实测）。

阶段 0 观察到的 UI 事件：

```text
message.delta
thinking.delta
thinking.completed
session.snapshot
session.queue
question.requested
question.resolved
```

没有独立的 `approval.requested` 事件；审批只从 snapshot 的 `pendingApprovals` 获取（与 9.1 一致）。

两个环境注意点：

- **UTF-8**：探针在 Git Bash 里直接打印中文路径时出现乱码，原因是探针进程的 stdout 不是 UTF-8，JSON 文件本身是 UTF-8。TUI 启动时应把自身 stdout/stderr 设为 UTF-8，并确认终端是 UTF-8；Orca 的 xterm.js 通常没问题，旧 Windows 控制台需要检查代码页。
- **历史**：重启 sidecar 后，同一 `workspaceRoot` 下 `session.list` 能读回探针会话，`session.open` 返回 `readOnly: false` 的完整 snapshot；换一个 workspace 查询返回空列表。这验证了 TUI 与 VS Code 共用 `%LOCALAPPDATA%\ling-agent\sessions.db` 的前提。

---

## 6. 分阶段实施计划

### 阶段 0：协议探针（先做，不改产品代码）

> 2026-11-19：已完成。真实 payload 见 2.2 和 5.7，产物在 `ling/build/tui-probe/`。后续实现直接以 5.7 为准，不要重新猜字段。

目标：把审批和问题的真实 payload 固化下来。

建议产物放在 `E:\毕业论文\ling\build\tui-probe\`（该目录已被 gitignore）：

1. 启动 sidecar，`LING_STORAGE_ROOT` 指向临时目录。
2. `initialize`。
3. 用 `manual` 模式让 Ling 在临时工作区创建一个文件。
4. 轮询/监听 `session.snapshot`，直到 `pendingApprovals` 非空。
5. 把第一个 approval 的完整 JSON 写入 `events.ndjson`。
6. 调 `approval.resolve` 允许一次，确认文件生成、run 完成。
7. 再跑一次，改为拒绝，确认文件未生成。
8. 尝试让 Ling 调用 `ask_user`，记录 `question.requested`，再调 `question.answer`。
9. 记录真实事件和错误，补充到本文档的“RPC 契约速查”里（如果和上面不一样，以真实值为准）。

验收：

- 能看到 `pendingApprovals[0]` 的 `id`、`effectDigest`、`arguments`、`allowAlways`。
- 允许/拒绝都能让 run 正常结束。
- `question.answer` 返回 `{"resolved": true}`。
- 不修改 `ling_rpc` 源码。

### 阶段 1：TUI 骨架 + 聊天（已实现）

- `src/rpc.ts`：sidecar 启动、initialize、request/response、事件队列。
- `src/config.ts`：读 config、CLI 覆盖、env 构建、exe/python 解析。
- `src/tui/App.tsx`：snapshot 替换、delta 追加、Ink 输入循环、`session.start`、Ctrl+C 取消。
- `src/tui/index.tsx`：启动 sidecar、初始化、`--resume`、渲染 `<App/>`。
- `src/index.ts`：argparse、`--check`、`--help`、`--version`。
- 支持中文输入/输出，流式显示 assistant 文本。
- 已实现完整审批/问题处理，见阶段 2。

验收（已完成）：

- `ling --check` 在真实 sidecar 上握手成功，isolated env 不会泄漏 shell 变量。
- `scripts/tui-smoke.mjs` 通过真实 sidecar：聊天 `Ling APPLE`、`/new`、`write_file` 审批、`/last` 展开 diff、`/history` 选择恢复会话。
- `npm test` 的 44 个测试通过（34 个 Ink/交互 + 4 个 config + 3 个 RPC + 3 个 Markdown 渲染）。
- 退出后 sidecar 进程结束，没有残留 python 进程。

### 阶段 2：工具 + 审批 + 问题（已实现）

- 渲染 tool entries；`/details`、`/last` 展开 diff/io，截断标记。
- 审批卡片：允许一次、拒绝、反馈、allowAlways mode/command。
- 写入/编辑的 diff 展示；`write_file` 内容、`edit_file` old/new diff、`run_command` 完整命令。
- 问题卡片：单选、多选、多问题、自定义、取消。
- 运行中 `Ctrl+C` 取消，并清理 pending 状态。
- Markdown 渲染 assistant 输出。

验收（已完成）：

- 单元测试覆盖允许/拒绝/反馈、allowAlways 两条路径、单选/多选/多问题/自定义。
- 真实探针（阶段 0）验证 sidecar 的允许/拒绝/命令审批/ask_user。
- 真实 TUI 冒烟验证写文件审批和 diff 展开。

### 阶段 3：历史 + 模式 + 队列 + 后台任务（已实现）

- `/history [query]`（↑/↓ + Enter）、`--resume`。
- `/mode` 切换，状态栏同步。
- 运行中提交消息入队，对话下方内联显示 `Queued · N`；Esc 中断并 promote 顶部（最新）消息。
- `background.updated`、`/tasks`、`/stop-task`；状态栏显示后台数量。
- `session.new` 清空状态。

验收（已完成）：

- 单元测试覆盖历史列表/打开、队列提升、后台任务列表/停止。
- 真实 TUI 冒烟覆盖 `/history` 选择恢复。
- List/Open 的 workspace 校验错误有清晰提示。
- 队列长度和后台状态来自 snapshot/事件；后台完成有 notice。

### 阶段 4：Orca 终端接入 + npm 分发 + 打磨

- npm 包 `ling-agent`：`bin.ling`、`files`、`vendor/win32-x64/ling-sidecar.exe`。
- 在 Orca 终端从工作区启动，验证滚动、复制、中文、退出恢复。
- 可选：`/thinking`、`/details`、`/last`、`/status`、`/clear`。
- 可选：调试日志、sidecar 重启提示、错误上报。
- 可选：启动时自动恢复最近会话（需谨慎，默认关闭）。
- 可选：MCP 状态、代码索引；图片附件已通过 `/attach` 接入。

验收：

- `npm i -g ling-agent` 后，在 Orca 终端里输入 `ling` 即可使用。
- 审批、问题、取消、历史都不需要离开终端。
- 不修改 Orca 源码。

---

## 7. npm 包与 sidecar 分发

不再使用 `ling-tui.cmd`。用户安装的是 npm 包，npm 自动生成 Windows 的 `ling.cmd` shim：

```json
{
  "name": "ling-agent",
  "version": "0.1.0",
  "type": "module",
  "bin": { "ling": "bin/ling.js" },
  "engines": { "node": ">=22" },
  "os": ["win32"],
  "cpu": ["x64"],
  "files": ["bin", "dist", "skills", "vendor", "README.md"]
}
```

当前先采用**单包内置**方案，等发布流程稳定后再拆平台包：

```text
ling-cli/
  bin/ling.js
  dist/...
  skills/...                         # 内置技能，打包时从 .agents/skills 复制
  vendor/win32-x64/ling-sidecar.exe  # 约 48 MB，打包时复制
```

- `scripts/copy-sidecar.mjs` 从 `ling-vscode/resources/sidecar/ling-sidecar.exe` 复制到 `vendor/win32-x64/`；如果文件不存在，提示先运行 `cd ling-vscode; npm run build:sidecar`。
- **不要复用旧的 `resources/sidecar/ling-sidecar.exe`**：2026-11-19 实测旧 exe 在含未完成工具调用的历史会话上会触发 HTTP 400；发布前必须先 `npm run build:sidecar`，再复制。
- `scripts/copy-skills.mjs` 从 `.agents/skills` 复制到 `skills/`，保证 npm 安装后内置技能可用。
- `vendor/` 和 `skills/` 都是构建产物，gitignore；`prepack` 会自动执行 build + copy-sidecar + copy-skills。

安装后的行为：

- npm 把包解到全局 `node_modules/ling-agent`；
- `ling` 由 `bin/ling.js` 启动，Node/TS TUI 通过 `child_process.spawn` 执行 `vendor/win32-x64/ling-sidecar.exe`；
- 用户不需要 Python；sidecar exe 内含 Python 运行时；
- sidecar 用 `--onefile` 打包，运行时解压到系统临时目录（通常 C:）。开发时直接跑 Python 源码可避免；正式分发包可接受。

平台策略：

- 目前只支持 `win32-x64`，与 VS Code Marketplace 的支持范围一致。
- 后续跨平台时，把 `vendor/<platform>-<arch>` 拆成 `@ling/sidecar-win32-x64`、`@ling/sidecar-darwin-arm64` 等 optionalDependencies，主包按 `process.platform`/`process.arch` 解析。
- 不要用 `postinstall` 从 GitHub 下载 exe 作为首选方案：代理、权限、CI 和可复现性都更差。随包分发更稳。

开发期：

```powershell
cd E:\毕业论文\ling-cli
npm install
npm run build
node bin/ling.js --check
node bin/ling.js --workspace E:\some-workspace
# 或 npm link 后在任意目录执行 ling
```

发布前：

```powershell
npm run build
npm run build:sidecar
npm run build:skills
npm pack --dry-run   # 检查文件清单/体积（prepack 会自动再执行一次）
npm publish          # 需要 npm 2FA/trusted publishing，当前尚未执行
```

---

## 8. 测试计划

### 8.1 单元测试（已实现，`npm test` 44 个通过）

`src/tests/app.test.tsx`（22 个，`ink-testing-library` + 假 `SidecarClient`）：

- 渲染初始对话、Markdown、流式 delta；验证 `User:` / `Ling:` 标签与首行回答同行、内容列对齐。
- 提交 `session.start`，检查 prompt/workspace/mode/reasoning。
- 审批：允许、拒绝+feedback、allowAlways mode（setMode 顺序）、allowAlways command（remember）。
- 问题：单选、多选、多问题、自定义回答。
- `/` 命令菜单、Tab 补全、Enter 执行；Shift+Tab 切换模式。
- `/last` 打开 tool detail overlay。
- 队列内联显示（最新在上）；Esc 中断并 promote 顶部（最新）消息；队列为空时 Esc 取消 run。
- `/history` 选择恢复、`/tasks` + `/stop-task`。

`src/tests/config.test.ts`（4 个）：

- 配置读取、缺 key、非法 JSON、shell `LING_*` 清理、storage root、显式 sidecar 解析。

`src/tests/rpc.test.ts`（3 个）：

- 请求/响应 id 匹配、`ui.event` / `index.progress` 通知、超时、sidecar 退出时 pending 报错。

CI：`.github/workflows/ling-cli.yml` 在 Windows 上跑 `npm ci` + `npm test`，只改 `ling-cli/**` 时触发。

### 8.2 集成测试（已实现）

`scripts/tui-smoke.mjs` 用真实 sidecar + Ink 测试渲染器：

- 隔离 storage/workspace，不碰 VS Code 历史；
- 启动 sidecar、initialize、渲染 `<App/>`；
- 输入消息并 Enter，等待界面出现 `Ling APPLE` 且 phase 回到 `idle`；
- 发送 `/new` 清掉对话，再让 agent 创建文件；等待审批卡片，按 `1` 允许，检查文件和 idle；
- `/last` 展开工具 diff，检查 `+ TUI_ALLOW`；
- `/history` 列表，↑/↓ 选择并按 Enter 恢复当前会话；
- 结束 sidecar，检查无残留进程。

该测试调用真实模型，不适合 CI 每次跑，作为发布前/改动 RPC 后的冒烟测试。CI 用 8.1 的假客户端测试即可。

`ling --check` 是更轻的集成检查：真实 sidecar 握手、ping、snapshot、shutdown，不调用模型。

### 8.3 手工验收

在 Orca 终端或 Windows Terminal 中：

1. 启动，发中文消息，流式输出正常。
2. Manual 模式创建文件，审批卡片出现；拒绝；文件不存在。
3. 再次创建，允许；文件存在；工具卡片 Completed。
4. `ask_user` 问题出现，选择答案，run 继续。
5. 运行中按 Ctrl+C，状态变为 cancelled。
6. 运行中再发一条，显示入队；run 结束后自动执行。
7. `/mode accept-write` 后写入不再询问；`/mode manual` 后恢复询问。
8. `/history` 打开历史，继续对话。
9. `/new` 清空会话，mode 回到 manual。
10. 退出后检查没有残留 sidecar 进程。
11. 在 Orca 终端里滚动、复制、粘贴、中文输入正常。
12. 关闭 Orca 终端或 Ctrl+C 后，终端状态没有损坏（光标/颜色/echo 正常）。

---

## 9. 已知风险与处理

### 9.1 审批只在 snapshot 里

当前 RPC 没有单独的 `approval.requested` UI 事件；审批通过 `session.snapshot` 的 `pendingApprovals` 出现。  
TUI 不要假设会有 `approval.requested` 通知；以 snapshot 为准。  
`question.requested` 有独立事件，但 snapshot 里的 `pendingQuestions` 也要作为兜底。

### 9.2 `approval.resolve` 不是“永久允许”

`approved: true` 只代表本次允许。  
写文件的“以后都允许”必须通过 `session.setMode("accept-write")`。  
命令的“以后不再问”通过 `remember: true` 写入 workspace 的 `.agents/settings.local.json`。  
不要把 `remember` 当成通用的 always-allow。

### 9.3 并发客户端

不要在同一个工作区同时用 VS Code 和 TUI 操作同一个活动会话。  
每个 sidecar 有自己的活动 agent；历史数据库是共享的，但运行态不共享。  
如果必须同时开，至少使用不同的工作区，或在 TUI 里只做只读历史查看。

### 9.4 Orca 终端快捷键

Orca 可能拦截部分快捷键（例如终端面板快捷键）。  
所有核心操作都要有斜杠命令兜底；不要只依赖单键。  
如果 Shift+Enter 不可用，使用 Alt+Enter / Ctrl+J。

### 9.5 终端渲染

- 不做图片内联；图片和文件通过 `/attach <path>` 附加，终端不能直接粘贴剪贴板图片。
- 长 diff / 长工具输出必须截断，并标记截断。
- Markdown 流式渲染可能闪烁；阶段 1 先用纯文本流，后续再上 Markdown。
- 已完成 entry 通过 Ink `<Static>` 写入 scrollback；动态区域只保留当前 run 和交互控件。底部状态/输入框在 Orca 连续 resize 时仍可能叠帧，属于已知未解问题；暂缓处理。
- 退出前确保 `unmount()` Ink 实例并恢复 raw mode/光标，否则终端会残留控制序列。

### 9.6 依赖与打包

- TUI 依赖 `ink`、`react`、`marked`、`diff`，放在 `ling-cli/package.json`；不要把它们塞进 sidecar 的 PyInstaller 包。
- sidecar 的 Python 依赖继续在 `ling/requirements.txt`；TUI 不修改它。
- `node_modules`、npm cache、临时文件都放 E:（本机使用 `E:\npm-cache` / `E:\npm-tmp` 安装）。
- Node >= 22（Ink 8 的要求）；`package.json` 的 `engines` 要写清楚。
- 发布包约 48 MB 的 sidecar exe；按平台拆包，不要把多个平台塞进一个包。
- `--onefile` sidecar 运行时会解压到系统临时目录；开发时用 Python 源码，正式分发接受这一行为。

### 9.7 不修改 ling_rpc

优先只加 TUI 客户端。  
如果 TUI 发现某个数据缺失（例如审批 diff 的历史内容），优先在客户端用已有 `arguments` 和 `changes.*` 解决。  
只有在确实无法实现时，才考虑给 `ling_rpc` 增加**向后兼容的**只读字段/方法，并保持协议版本 1；不要在 TUI 阶段重构 sidecar。

---

## 10. 给实现者的检查清单

开始前：

- [ ] 读 `AGENTS.md` 的约定和“容易踩的坑”。
- [ ] 读 `ling_rpc/server.py` 的 method 分发和参数校验。
- [ ] 读 `ling_rpc/ui_events.py` 的 snapshot 结构和事件类型。
- [ ] 读 `ling_rpc/questions.py` 的 `question.requested` / `question.answer`。
- [ ] 读 `ling_agent/approvals.py` 和 `ling_rpc/sessions.py` 的审批语义。
- [ ] 跑现有测试：`cd E:\毕业论文\ling; python -m pytest tests -q`。

实现中：

- [x] 先做阶段 0 探针，拿到真实审批/问题 payload（2026-11-19 完成，见 5.7）。
- [x] TUI 只依赖公开 JSON-RPC，不 import `ling_rpc.sessions`。
- [x] API key 不落日志（`redact` + 仅记录方法名/事件类型）。
- [x] 依赖、cache、临时文件在 E:。
- [x] 审批默认 manual，不提供绕过。
- [x] Ctrl+C / 退出时清理 sidecar 和终端状态。
- [ ] 对未知事件、过期 approval、sidecar 崩溃宽容处理（部分完成）。
- [x] 阶段 1 的 `ling --check`、Ink 聊天、审批/问题已跑通。
- [x] Markdown 渲染、tool/approval diff 与 `/details`、`/last`。
- [x] 审批拒绝反馈、allowAlways mode/command。
- [x] 多选、多问题、自定义回答、取消。
- [x] `/history` 选择、`/tasks`、`/stop-task`；队列内联显示 + Esc 中断/promote。
- [x] `config.ts` / `rpc.ts` 单元测试与 `ling-cli` CI。

完成标准（MVP）：

- [ ] `npm i -g ling-agent` 后，在 Orca 终端里能启动 `ling`（按用户要求暂缓打包/发布）。
- [x] 能连续多轮对话，流式输出中文正常（真实 sidecar 冒烟已通过）。
- [x] 能处理写文件/命令审批，允许/拒绝/反馈都有效（真实探针 + Ink 测试）。
- [x] 能回答 `ask_user` 问题（单选、多选、多问题、自定义）。
- [x] 能中断运行、切换模式、新建/恢复会话（Esc、`/mode`、`/new`、`--resume` 已实现）。
- [x] 能查看历史、队列和后台任务（`/history`、内联队列、`/tasks`）。
- [x] 工具卡片、Markdown、diff 和状态栏可读，不需要离开终端。
- [x] 退出后无残留进程，终端恢复正常。
- [x] 没有修改 Orca 源码。

---

## 11. 备注

- Orca 的终端是一个普通 PTY；它不需要知道 Ling 是 Agent，也能运行 TUI。代价是没有 agent picker 和原生状态集成。
- 如果未来要把 Ling 接进 Orca 的 agent picker，可以单独做 ACP 或 native adapter；那是另一条路线，不和本 TUI 冲突。
- 本文件是实现说明，不是最终 API 文档；阶段 0 探针得到的真实 payload 应回写到第 5 节。
