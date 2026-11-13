<p align="center">
  <img src="media/icon.png" width="72" alt="Ling" />
</p>

<h1 align="center">Ling</h1>

<p align="center">一个在 VS Code 里读代码、改代码、跑命令的 coding agent。</p>

Ling 在侧边栏里和你对话：它可以读文件、搜索代码、修改文件、执行命令、读取 PDF/DOCX/PPTX，也可以按项目风格为函数生成注释。所有写入和普通命令默认都会先经过审批；文件修改会用 VS Code 原生 diff 展示。

当前版本面向 **Windows x64**，要求 **VS Code 1.96+**。

## 功能

- **对话与工具**：流式回复、工具卡片、读文件、改文件、搜索、执行命令。
- **审批与 diff**：写入前打开原生 diff，允许或拒绝；普通命令和敏感操作默认需要审批。
- **计划模式**：只用只读工具调研，把实施计划写到 `.agents/plans/`。可以在编辑器里查看计划，用文件上的 `Build plan…` CodeLens 或聊天卡片的 Build 按钮选择模式执行。
- **注释生成**：在函数/方法/类上方点击 `Generate comment`，生成 Python docstring 或 TS/JS JSDoc。已有注释会一起发给模型做查漏补缺，生成后替换原注释。
- **上下文**：编辑器/终端选区、图片、文件、PDF/DOCX/PPTX。
- **Skills**：内置 `pdf`、`pptx`、`docx`、`inline-visualization`、`browser-bridge`、`frontend-design`，并加载全局 `~/.agents/skills` 和工作区技能；内置优先。
- **交互提问**：模型拿不定主意时，会弹出选项卡让你选择。
- **历史**：按工作区隔离，支持搜索、删除、单条消息删除和重新生成；重新打开面板时会自动恢复上一次会话。
- **无文件夹也能用**：没有打开任何文件夹时，Ling 使用一个固定的 scratch 工作区。
- **内联可视化**：模型输出 `html type="renderer"` 代码块时，直接在对话里渲染图表。

## 安装与配置

1. 在 VS Code Marketplace 安装 **Ling**。
2. 点击左侧活动栏的 Ling 图标打开 Chat；如果找不到，可以在命令面板运行 `Ling: Open Chat`。
3. 点击面板右上角的齿轮，填写 **API Key**、**Model**、**API Base URL**，然后保存。

默认模型是 `deepseek-chat`，默认地址是 `https://api.deepseek.com/v1`。任何兼容 OpenAI Chat Completions 的接口都可以使用。

配置保存在 Ling 自己的文件里：

- Windows：`%APPDATA%\Ling\config.json`

API Key 以明文保存在该文件中，不会写入项目文件、历史数据库或日志。

## 注释生成

把光标放在函数、方法或类里，函数上方会出现 `Generate comment`；也可以右键选择 `Generate Comment`，或在命令面板运行 `Ling: Generate Comment`。

支持：

- **Python**：生成 Google 风格 docstring。
- **TypeScript / JavaScript**：生成 JSDoc，包含 `@param` 和 `@returns`。
- **TSX / JSX**：同样支持。
- **箭头函数**：`const foo = (a: number) => ...` 也可以生成。

生成后会打开 diff 预览：

- 接受：`Ctrl+Alt+Enter`，或点 diff 标题栏的 `✓`。
- 拒绝：`Ctrl+Alt+Backspace`，或点 `✕`。
- 接受后注释会写入并**自动保存文件**；拒绝不会修改文件。

如果函数已经有 docstring 或 JSDoc，Ling 会把原注释一起发给模型，要求保留正确内容、补齐缺失部分，然后用新注释替换原注释。

限制：

- 只支持 Python、TypeScript、JavaScript、TSX、JSX。
- 文件需要先保存到磁盘。
- Python 单行函数体（`def f(): return 1`）不支持。
- Python 在没有语言服务器时使用文本扫描兜底；TS/JS 依赖 VS Code 自带的 TypeScript 语言服务。

## 对话与工具

在 Chat 里输入需求，Ling 会自主决定调用哪些工具：

- 读取文件和目录；
- 搜索文本；
- 写入或修改文件；
- 执行命令；
- 读取 PDF、DOCX、PPTX；
- 加载技能；
- 向你提问。

工具执行会显示成卡片，可以展开查看参数和结果。运行中的回复可以随时点输入框右侧的 **Stop** 停止；停止后可以直接发下一条消息。

### 探索子 agent

遇到需要浏览大量文件、但中间结果会挤满对话的调研任务时，Ling 会把任务委派给一个只读的 explore subagent：

- 子 agent 只能读取、搜索和浏览文件，不能修改文件、执行命令或向你提问；
- 它使用独立的上下文，只把结论摘要带回主对话；
- 聊天里会显示 **Subagent** 卡片，运行中展开可以看到实时的工具调用和中间输出；
- 完成后卡片显示结论摘要，点 **View trace** 可以回看完整的执行记录。

子 agent 完成后主对话才会继续，因此适合需要立即拿到调研结果的场景。

### 浏览器（Kro Bridge）

Ling 可以直接驱动你正在使用的 Edge 浏览器。需要先在 Edge 安装 **Kro Browser Bridge** 扩展；Ling 的 sidecar 会自己启动本机 bridge，不经过 MCP。

- 第一次调用 `browser_bridge` 时才监听 `127.0.0.1:17323`；
- 打开扩展，Port 选 Auto detect 或填 `17323`，点 Reconnect 直到 Connected；
- 同一时间只能有一个 bridge 占用 17323。如果 Claude Code 的 `kro-bridge` MCP 也在运行，需要先移除或停止它；
- 模型会先检查 `extension_status`；扩展未连接时会停下来让你处理，不会自动降级到其它浏览器；
- 点击、输入、打开/关闭标签页等操作需要审批；`execute_script` 始终需要审批；
- Plan 模式只允许 `extension_status`、`list_pages`、`read_state`、`extract` 等只读动作。

### MCP

MCP 服务器是用户级配置，所有工作区共享：

- 配置：`%APPDATA%\Ling\mcp.json`
- OAuth 凭证：`%LOCALAPPDATA%\ling-agent\mcp-auth\`
- 工具清单缓存：`%LOCALAPPDATA%\ling-agent\mcp-cache.json`

支持 stdio、Streamable HTTP、SSE fallback、静态 headers 和 PKCE OAuth；Windows 凭证使用 DPAPI 加密。Settings → MCP 可以打开配置文件、启用/停用服务器或重新授权。

### 审批模式

输入框右下角的模式按钮里可以切换，也可以直接按 **Shift+Tab** 循环切换四种模式：

- **Manual**：写入和普通命令都需要审批。
- **Edit automatically**：工作区写入直接允许，普通命令仍然需要审批。
- **Auto**：普通操作直接允许，危险命令和敏感路径/内容写入仍然需要审批。
- **Plan**：只读探索，不修改文件也不执行命令；模型调用 `create_plan` 把计划写到 `.agents/plans/`，然后等待 Build。

Plan 模式下，计划文件顶部会出现 `Build plan…` CodeLens；聊天里的计划卡片也有 Build 按钮。Build 前会先选 Manual / Edit automatically / Auto，然后读取文件当前内容执行——在编辑器里改过的计划会原样生效。

审批模式按会话保存；新建会话会回到 Manual。

### Reasoning effort

同一个模式菜单里还有五档 effort：

```text
Low · Medium · High · Extra high · Max
```

默认是 **High**。effort 越高，模型花在推理上的 token 越多；选择结果会保存到 Ling 配置，并随每条消息发送，不需要重启。

### 文件修改 diff

`write_file` / `edit_file` 等待审批时，会打开 VS Code 原生 diff：

- 左侧是磁盘上的原文件，右侧是拟议内容；新建文件左侧为空。
- 默认使用 Inline View（单栏红绿）。这个行为由 VS Code 设置 `ling.diff.inlineView` 控制，关闭后使用并排视图。
- 可以在 Ling 的审批卡片里选择 `Allow once` / `Deny`，也可以在 diff 标题栏点 `✓` / `✕`。
- 文件超过 1 MB、二进制内容、找不到 `old_text` 等情况不显示 diff，但审批流程仍然有效。
- `run_command` 造成的修改不会显示 diff。

### 消息操作

- 用户消息和模型回复悬停后可以复制、删除；删除需要二次确认。
- 最新一条模型回复可以重新生成。
- 运行中不能删除或重新生成。

## 上下文

### 编辑器与终端选区

- **编辑器**：选中代码后，选区首行出现 `Add N lines to Ling chat`；也可以右键选择 `Add Editor Selection to Ling Chat`。
- **终端**：选中终端文本后右键 `Add Terminal Selection to Ling Chat`。
- **聊天页**：选中消息文字后，可以 `Copy` 或 `Quote` 到输入框。

每条消息最多 4 个选区，每个选区最多 50,000 字符。编辑器来源的选区在历史消息里可以点击回到原位置。

### 图片与文件

输入框左下角的 `+` 可以选择文件；也可以直接粘贴或拖拽图片。

- 每条消息最多 4 个附件（图片和文件合计）。
- 图片：PNG、JPEG、WebP、GIF、BMP，单张最大 4 MB。
- 图片会复制到工作区的 `.agents/attachments/`，消息里只保存引用。
- 其他文件：不复制，单文件最大 25 MB；Ling 在需要时通过工具读取。
- 纯图片消息也可以发送。

建议将 `.agents/attachments/`、`.agents/spills/`、`.agents/code-index/` 加入项目的 `.gitignore`；`.agents/memory/` 和 `.agents/skills/` 可以纳入版本控制。

### 文档读取

`extract_document_text` 支持 PDF、DOCX、PPTX：

- 支持工作区相对路径和绝对路径。
- `page_from` / `page_to` 对 PDF 和 PPTX 生效（从 1 开始，包含端点）。
- 输出包含正文、是否截断、页数和警告；PDF 用 `--- Page N ---`，PPTX 用 `--- Slide N ---` 分段。
- DOCX 会保留标题和表格；PPTX 按段落合并文本。
- 单文件最大 25 MB。
- 不支持 `.doc` / `.ppt` / `.xlsx` 和图片 OCR；扫描版 PDF 会提示没有文本层。

## Skills

Ling 从三个位置加载技能，优先级从高到低：

- 插件内置：`<extension>/resources/skills/<name>/SKILL.md`
- 全局用户技能：`~/.agents/skills/<name>/SKILL.md` 中没有 `source: ling` 的技能
- 工作区技能：`<workspace>/.agents/skills/<name>/SKILL.md`

内置技能优先级最高，同名的工作区或全局技能不会覆盖它。插件启动时会把内置技能同步到全局库，并在 `SKILL.md` 里标记 `source: ling`；升级只覆盖这些托管技能，而且会先比对 hash，用户改过的副本保持不动。未标记的目录永远不碰。要定制内置技能，请复制一份并改成不同的 `name`。

Ling 只会把技能的 `name` 和 `description` 放进上下文；模型判断需要时调用 `load_skill` 读取完整内容，再用 `skill_path` / `scripts_path` 执行脚本。

内置技能：`pdf`、`pptx`、`docx`、`inline-visualization`、`browser-bridge`、`frontend-design`。

PDF/PPTX/DOCX 的生成和转换脚本依赖外部工具（如 `uv`、pandoc、LibreOffice、Chrome）。缺少这些工具时，命令会返回明确的失败信息，不影响技能加载。

## 交互提问

模型需要你决定时，会在输入框上方显示问题卡片：

- 一次最多 4 个问题，每题最多 10 个选项。
- 支持单选、多选和自定义文本回答。
- 回答会作为工具结果返回给模型。

## 执行中的消息

任务执行中仍然可以继续输入：

- 新消息会进入队列（最多 20 条），当前任务结束后按顺序发送。
- 输入框上方显示排队消息；点击 `Send now` 会中断当前任务并立即发送该条。
- `Stop` 或 `Cancel run` 会同时清空队列。
- 队列只保存在内存里，Ling 重启后丢失。

## 历史

点击 Chat 顶部的历史按钮可以查看当前工作区的会话：

- 支持按标题、消息正文和附件名搜索。
- 支持删除会话；删除前会在列表行内确认。
- 打开历史会恢复用户消息、模型回复、工具卡和审批记录；继续发送会复用同一个会话。
- 会话按工作区隔离，不会出现在其他项目的列表里。

## 无文件夹工作区

没有打开任何文件夹时，Ling 会把一个固定目录当作工作区：

- Windows：`%LOCALAPPDATA%\ling-agent\scratch`

聊天、附件、历史、文件工具和命令工具都会在这个目录里工作；Chat 顶部会显示 `No folder`。没有文件夹时，文件修改的 diff 预览不会自动打开，审批仍然在 Ling 的卡片里完成。

## 上下文治理

长会话不会把完整的工具输出一直塞进模型窗口：

- 超过 12,000 字符的工具结果会写入 `.agents/spills/<session>/<call>.txt`，模型只看到约 2,000 字符预览和文件路径；单条工具结果上限 50,000 字符。
- 接近上下文窗口时，较早的历史会被折叠成摘要，模型看到“摘要 + 最近完整消息”；默认保留最近 5 个用户轮。
- 完整历史仍然保留在本地数据库和界面里，对话界面只增加一条 `Context compacted · N messages folded` 标记。
- 上下文窗口和保留轮数可以在 Settings 里调整。

## 内联可视化

模型输出下面这种代码块时，Ling 会把它渲染成图，而不是显示源码：

````text
```html type="renderer"
<svg ...>...</svg>
```
````

- 流式期间约每 70ms 更新一次。
- 在沙箱 iframe 中渲染，不执行脚本，禁止外部资源。
- 高度随内容自动调整，宽度跟随消息区。
- 右上角提供 `Copy`（复制源码）和 `Source`（展开源码）。

只有带 `type="renderer"` 的 `html` 代码块会渲染，普通代码块不变。

## 设置

点击 Chat 右上角的齿轮打开 Settings：

| 设置 | 默认值 | 说明 |
|---|---|---|
| API Key | 空 | 留空保存表示删除已保存的 Key |
| Model | `deepseek-chat` | 模型名称 |
| API Base URL | `https://api.deepseek.com/v1` | OpenAI-compatible 地址 |
| Reasoning effort | `High` | Low / Medium / High / Extra high / Max |
| Diff view | Inline | 或 Side by side |
| Max steps per message | 50 | 1–100 |
| Parallel tools | 4 | 1–16 |
| Command timeout | 120s | 1–3600 |
| Approval timeout | 120s | 1–3600 |
| Context window | 1,000,000 | 1024–10,000,000 |
| Keep recent user turns | 5 | 0–50 |
| Embedding model | `text-embedding-v4` | 换服务商时修改 |
| Embedding base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 换成其它兼容服务时修改 |
| Embedding API Key | 空 | 留空复用 API Key |
| Skills | — | 只读列表，显示来源和路径 |

保存后 Ling 的后台进程会自动重启，下一次请求生效。运行中的任务不能打开 Settings。

语义检索是可选的。DeepSeek 没有 embeddings 接口，需要另外配置一个支持的服务，例如：

- 阿里云百炼：Base URL `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `text-embedding-v4`
- 硅基流动：Base URL `https://api.siliconflow.cn/v1`，模型 `BAAI/bge-m3`

配置后，`search_codebase` 会先用 BM25 和向量各取一批候选，再用 RRF 融合；模型、文档改动
或索引变化时会按 chunk 增量补算，只改一个函数不会重算整个仓库。没有配置或调用失败时自动退回
纯关键词检索，不影响工具使用。切换 embedding 模型会清空旧向量并重建。

首次搜索会同步建立向量索引：几百个 chunk 大约几十秒，之后都是增量更新。工具结果里的
`mode` 会显示 `hybrid` 或 `bm25`。

Settings 底部的 **Code index** 也可以手动触发（命令面板里是 `Ling: Build Code Index`）。
它和搜索时用的是同一套增量逻辑：文件 mtime/size 没变就跳过；文件变了也只重算
`content_hash` 变了的 chunk，其它函数的向量保留。没有配置 embedding 时只建关键词索引。

## 隐私与安全

- API Key 以明文保存在 `%APPDATA%\Ling\config.json`，不会写入项目文件、历史数据库或日志。
- 模型请求直接发往你配置的 API 地址；对话内容、附件、工具输出和工作区上下文都可能被发送给该服务商。
- 启用语义检索后，建立索引时会把代码 chunk 的正文发送到你配置的 embedding 服务；不配置则只做本地关键词检索。
- Ling 不提供云端代理，也没有遥测。
- 写入工作区外的文件默认会被阻止；`write_file` / `edit_file` 只能修改工作区内的路径。
- `run_command` 可以执行任意 PowerShell 命令，但受审批模式约束；危险命令和敏感操作始终需要审批。
- 图片附件会复制到项目的 `.agents/attachments/`；对话历史保存在 `%LOCALAPPDATA%\ling-agent\sessions.db`。

## 已知限制

- 当前 Marketplace 发布只支持 **Windows x64**。
- 注释生成只支持 Python、TypeScript、JavaScript、TSX、JSX；Python 单行函数体不支持。
- 文档读取不支持 OCR，也不支持 `.doc` / `.ppt` / `.xlsx`。
- 技能里的 PDF/PPTX/DOCX 生成脚本需要额外安装外部工具。
- `run_command` 造成的文件修改不会显示 diff。
- 执行中的消息队列只保存在内存里，重启后丢失。
- 上下文压缩的保留输出、缓冲区等内部参数尚未暴露到 Settings。

## 反馈

遇到问题或有功能建议，请在 GitHub 提交 issue：

https://github.com/guyi-a/ling-agent-vscode/issues

## 开发

```powershell
cd ling-vscode
npm install
npm run build
npm test
```

构建 Python 运行时：

```powershell
npm run build:sidecar
```

用 VS Code 打开 `ling-vscode` 目录，按 `F5` 启动 Extension Development Host。

## License

MIT
