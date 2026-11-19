# ling-agent

Ling 的终端客户端（TUI）。它通过 stdio JSON-RPC 连接 Ling sidecar，在终端里对话、查看工具调用、处理审批和 `ask_user` 问题。

## 状态

- 版本 `0.1.1`，通过 GitHub Release 分发；**尚未发布到 npm**。
- 仅支持 Windows x64，要求 Node.js >= 22。
- 复用 Ling 的配置和会话历史：`%APPDATA%\Ling\config.json`、`%LOCALAPPDATA%\ling-agent\sessions.db`。
- sidecar 以 `vendor/win32-x64/ling-sidecar.exe` 随包分发，用户不需要安装 Python。

## 开发

```powershell
cd E:\毕业论文\ling-cli
npm install
npm run build
node bin/ling.js --check
node bin/ling.js --workspace E:\some-workspace
```

`npm run dev -- --workspace E:\some-workspace` 可以直接用 `tsx` 跑源码。

测试：

```powershell
npm test
```

真实 sidecar 的冒烟测试（会调用模型）：

```powershell
npm run build
node scripts/tui-smoke.mjs
```

## 使用

工作区目录下：

```powershell
ling                 # 新建会话
ling --continue      # 继续该工作区最近一次会话
ling --check         # 只握手并打印 snapshot，不进入 TUI
ling --help
```

常用斜杠命令：

```text
/help   /new   /status   /quit
/mode [manual|accept-write|auto|plan]
/effort [low|medium|high|xhigh|max]
/history [query]      # ↑/↓ 选择，Enter 打开
/config               # 编辑 Ling 配置
/tasks                /stop-task <taskId>
/details [n]          /last
/attach <path>        /detach
/mcp                  /index
/index-status         /changes [runId]
```

界面支持：

- assistant 输出的 Markdown（标题、粗体、列表、代码块、链接等）。纯文本回合 `Ling` 标签与回答首行同行；含工具的回合按 `runId` 归组，`Ling:` 作为标题，工具卡片和回答缩进在下面。列表按显示宽度预折行，CJK 不会把单个字符挤到最左侧。
- `type="renderer"` 的内联可视化代码块不打印源码，只显示 `[inline visualization · N lines · open in VS Code to view]` 占位。
- 用户消息用绿色 `User:` 标签，assistant 用青色 `Ling:` 标签；两个标签等宽，内容从同一列开始。
- 输入 `/` 弹出命令菜单：↑/↓ 选择，Tab 补全，Enter 执行，Esc 关闭；命令名和说明使用不同颜色。
- 已完成消息写入 scrollback；**底部动态区域在 Orca 连续拖拽 resize 时仍可能叠帧，这是已知未解问题**（用户暂缓处理）。
- 状态信息（mode/workspace/model/phase）在输入框内部；输入框边框颜色跟随审批模式；原生闪烁光标已隐藏。
- 输入框边框/标签颜色跟随审批模式：manual 蓝、accept-write 黄、auto 绿、plan 品红。
- 工具卡片、`/details`、`/last` 的 diff/io overlay。
- `Ctrl+Alt+T` 展开/收起最近一轮的 thinking 块；展开后思考内容和回答都缩进在 `Ling:` 回合内。
- `/attach <path>` 把文件或图片附到下一条消息，`/detach` 清空；图片会复制到 `.agents/attachments/`。终端只能传路径，不能直接粘贴剪贴板图片。
- `/mcp` 查看 MCP 服务器、工具数量和连接状态；`/index` 构建/刷新代码索引（会显示进度），`/index-status` 查看索引统计。
- `/changes [runId]` 查看最近一次 run（或指定 run）修改的文件和 `+added −removed`。
- 审批：允许一次、allowAlways mode/command、拒绝、带反馈拒绝。
- `ask_user`：单选、多选、多问题、自定义回答；Esc 中断当前 run。
- 运行中发送的消息在对话下方显示为 `Queued · N`（最新在上）；按 Esc 中断当前 run 并 promote 顶部（最新）消息。
- 历史、后台任务使用 overlay；`/history` 里用 ↑/↓ 选中某条记录后按 Enter 直接恢复会话。
- `/config` 在输入框里编辑 Ling 配置：↑/↓ 选字段，Enter 编辑，Esc 取消/关闭；API Key 显示为 `set/unset`，输入时用 `*` 遮挡。保存后需要重启 `ling` 生效。

快捷键：Enter 发送，Shift+Tab 循环切换审批模式，Ctrl+Alt+T 展开/收起 thinking，Esc 中断/关闭，Ctrl+C 取消/退出，Ctrl+D 退出。

## 配置

配置文件和 VS Code 版共用：

- Windows：`%APPDATA%\Ling\config.json`
- 模型、API Key、Base URL、API 协议等都从该文件读取，CLI 参数可以覆盖模型/协议/Base URL。
- 没有配置文件时 TUI 仍能启动；在 TUI 里输入 `/config` 即可创建并编辑，保存后重启 `ling` 生效。
- shell 里的 `LING_*`、`DEEPSEEK_*`、`OPENAI_*` 会被清掉，避免旧值串进 sidecar。
- `/config` 只更新配置中对应的字段，保留未知字段；文件写入使用临时文件替换，并尝试设为 0600。
- API Key 不会出现在终端输出、日志或 `--debug-log` 中；`/config` 列表只显示 `set` / `unset`。

## 架构

```text
ling (Node/TypeScript + Ink)
  └─ NDJSON JSON-RPC over stdio
       └─ ling-sidecar.exe (PyInstaller, Python harness)
```

- `src/config.ts`：配置读取、CLI 覆盖、sidecar 环境与启动方式。
- `src/rpc.ts`：子进程 RPC 客户端。
- `src/tui/App.tsx`：Ink 界面、输入、流式事件、审批/问题、历史/队列/后台。
- `src/tui/markdown.tsx`：把 `marked` token 渲染成 Ink 组件。
- `src/tui/detail.tsx`：工具 diff/io 和审批命令/内容/diff。
- `src/tui/index.tsx`：启动 sidecar、初始化、渲染。

## 打包

```powershell
cd E:\毕业论文\ling-vscode
npm run build:sidecar

cd ..\ling-cli
npm run build
npm run build:sidecar   # 复制 exe 到 vendor/win32-x64
npm run build:skills    # 复制 .agents/skills 到 skills/
npm pack --dry-run      # prepack 会自动再执行上面三步
```

`vendor/` 和 `skills/` 是构建产物，不提交到 git。

## 测试

```powershell
npm test
```

当前 47 个测试：35 个 Ink/交互测试、3 个 Markdown 渲染测试、6 个 config 测试、3 个 RPC 测试。CI 在 `.github/workflows/ling-cli.yml` 里跑 `npm ci` + `npm test`。

真实 sidecar 冒烟（调用模型）：`node scripts/tui-smoke.mjs`，覆盖聊天、审批、`/last` diff、`/history` 选择恢复。

## 限制

- 仅 Windows x64；跨平台需要拆出 `@ling/sidecar-<platform>-<arch>` 平台包。
- 输入仍是单行；Alt+Enter 多行输入待补。
- 已完成消息已用 `<Static>` 写入 scrollback；动态区域只保留当前 run 和输入控件。
- 不做图片内联；附件通过 `/attach` 按路径接入，代码索引未接。
- Ctrl+Alt+T 只能展开最近一轮的 thinking；更早的历史 thinking 已写入 scrollback，不能原地重排。
- 不要和 VS Code 同时操作同一个活动会话；历史库共享，运行态不共享。
