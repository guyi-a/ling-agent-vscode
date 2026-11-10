# Ling VS Code Extension

当前阶段包含 A5 第 1–3 步、sidecar 握手和 Session 事件桥：VS Code 中已经可以运行真实 `CodingAgent`，并把流式文本、工具状态、审批、取消和错误映射到 Webview。浏览器预览仍使用脚本化事件。

Assistant 消息使用 GFM Markdown 渲染，支持标题、列表、表格、引用、链接、代码块和行内代码。表格与代码块在窄侧栏内独立横向滚动。

新会话页面会显示当前 workspace、一个明确主问题和三条快捷任务。顶部不再重复显示产品标题；Composer 固定在工作区底部。

Header 中的历史按钮会列出 SQLite 中保存的会话。History 面板支持按标题和消息正文搜索，也支持删除会话；删除前会在列表行内展开确认。打开历史时会从持久化事件恢复完整用户消息、Assistant 回复、工具卡和审批记录；继续发送消息时会重建原 Session 上下文并复用同一个 `session_id`。运行中的任务不能切换或删除历史。

运行期间仍可打开历史列表，但需要先停止当前任务才能切换会话。发送新用户消息后，Timeline 会自动滚动到底部。

## 浏览器预览

```powershell
cd ling-vscode
npm install
npm run dev:webview
```

浏览器预览由 Webview 自己播放脚本化事件流；VS Code 预览由 Extension Host 播放同一组事件，用来验证双向传输。

## VS Code 预览

1. 用 VS Code 打开 `ling-vscode` 目录。
2. 按 `F5` 启动 Extension Development Host。
3. 点击 Activity Bar 中的 Ling 图标。

VS Code 可能把自定义 View Container 放在左侧。需要右侧体验时，可以右键 Ling 容器并选择移动到 Secondary Side Bar；正式分发前再处理默认布局体验。

开发态默认从同级 `../ling` 目录运行 `python -m ling_rpc`。也可以通过 `LING_PYTHON_PATH` 指定 Python，或使用 `LING_SIDECAR_PATH` 指定已打包的 sidecar 可执行文件。

运行命令 `Ling: Check Sidecar` 可以手动执行重启、初始化和 ping。

发送第一条消息前需要打开一个 workspace，并配置自己的 API Key：

1. 点击 Ling 面板右上角的齿轮按钮
2. 在 Settings 面板填写 API Key、Model 和 API Base URL
3. 点击 Save；sidecar 会自动重启

配置保存在 Ling 自己的 JSON 文件：

- Windows：`%APPDATA%\Ling\config.json`
- macOS：`~/Library/Application Support/Ling/config.json`
- Linux：`$XDG_CONFIG_HOME/ling/config.json`，默认 `~/.config/ling/config.json`

首次启动时如果 JSON 里还没有 Key，会从 `LING_API_KEY`、`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 迁移一次；之后 Ling 只读这个 JSON，不再读取这些模型相关环境变量。

Key 明文保存在用户配置文件中，不写入项目文件、SQLite 或日志。保存或清除 Key 后 sidecar 会自动重启。

`LING_STORAGE_ROOT` 仍可用于指定 Session SQLite 存储目录。

## 图片与文件附件

Composer 支持：

- 粘贴或拖拽图片；
- 点击左下角加号选择任意文件。

图片：

- 单条消息最多 4 个附件，单张最大 4 MB。
- 支持 PNG、JPEG、WebP、GIF、BMP。
- 图片保存到 workspace 的 `.ling/attachments/`，消息本身只携带 attachment ref。
- 发送时 sidecar 从 workspace 读取 bytes，作为图片内容发给模型。
- 聊天区的用户消息会显示缩略图，点击可在 VS Code 中打开原图。
- 纯图片消息也可以发送。

其他文件：

- 不复制文件，单文件最大 25 MB。
- 消息只携带原始磁盘绝对路径和类型，不把文件内容塞进 prompt。
- agent 需要时通过工具或命令读取；工作区外的路径通常需要命令审批。
- 聊天区显示文件卡片，点击可在 VS Code 中打开原文件。
- PDF、DOCX、PPTX 可以用 `extract_document_text` 直接读取正文；`.doc`/`.ppt` 需要先转换为新格式，扫描版 PDF 暂不做 OCR。
- `read_file` 只适用于文本文件；工作区外的技能文件和附件路径有单独的只读白名单。

图片输入需要模型支持视觉。当前默认的 `deepseek-flash` 支持图片；如果选择的是纯文本模型，Ling 会提示切换到支持视觉的模型，而不是直接显示 provider 原始错误。建议把 `.ling/` 加入项目 `.gitignore`。

## 内联可视化

如果工作区里加载了 `inline-visualization` skill，模型会用下面这种围栏输出图：

````text
```html type="renderer"
<svg ...>...</svg>
```
````

Ling 会把它渲染成对话内的图，而不是显示源码：

- 流式期间每约 70ms 更新一次，元素完整一块就渲染一块；
- 渲染在沙箱 iframe 中，不执行脚本，禁止外部资源；
- 自动根据内容高度撑开，宽度跟随消息区；
- 右上角工具栏提供 `Copy`（复制源码）和 `Source`（展开源码）；
- 只有带 `type="renderer"` 的 `html` 围栏会渲染，普通代码块不变。

图内文字不参与聊天选区（iframe 隔离）；需要复制时用工具栏的 `Copy`。

## 文件修改 Diff

`write_file` / `edit_file` 等待审批时，编辑器会打开原生 diff：

- 左边是磁盘上的原文件，右边是拟议内容；新建文件左边为空。
- 默认切到 Inline View（单栏红绿 diff），由 `ling.diff.inlineView` 控制。开启时会把 VS Code 的 `diffEditor.renderSideBySide` 切到 `false`，影响所有 diff；不想要可以关掉这个设置。
- 审批可以在 Ling 卡片里 `Allow once` / `Deny`，也可以在 diff 编辑器标题栏点 `✓` / `✕`；快捷键是 `Ctrl+Alt+Enter` 允许、`Ctrl+Alt+Backspace` 拒绝（macOS 用 `Cmd+Alt+...`）。决定后 diff 关闭。
- 只覆盖 `write_file` / `edit_file`；`run_command` 造成的修改不会出 diff。
- 文件超过 1 MB、二进制内容、`edit_file` 找不到 `old_text` 时不显示 diff，但审批流程不受影响。

## 选区上下文

三个来源都可以把选中文本加到 Composer：

- **编辑器**：选中代码后，选区首行出现 `Add N lines to Ling chat` CodeLens；也可以右键选择 `Add Editor Selection to Ling Chat`。
- **终端**：选中终端文本后右键 `Add Terminal Selection to Ling Chat`。VS Code 没有终端选区 API，这里通过复制选区到剪贴板再读回来实现，读取后会恢复原剪贴板。
- **聊天页**：在消息正文里选中文字后，选区旁浮出 `Copy` / `Quote` 两个操作；`Copy` 复制到剪贴板，`Quote` 把选区加进 Composer。

添加后 Composer 显示 `N lines selected · 来源` 的 chip，可移除；最多 4 条，单条上限 50,000 字符。发送时选区文本注入模型上下文，气泡里只显示 chip；编辑器来源的 chip 在历史消息中可点击回到原文件位置。

## Skills

Ling 从两个位置加载技能：

- 插件内置：`<extension>/resources/skills/<name>/SKILL.md`
- 工作区覆盖：`<workspace>/.agents/skills/<name>/SKILL.md`

同名技能以工作区版本为准。系统提示只注入技能的 `name` 和 `description` 索引；模型判断任务匹配后调用 `load_skill(name=...)` 读取完整正文，再用 `skill_path` / `scripts_path` 执行脚本。技能目录不会被复制到工作区。

当前内置 `pdf`、`pptx`、`docx`、`inline-visualization` 四个技能。PDF/PPTX/DOCX 的生成/转换脚本依赖 `uv`、`pandoc`、LibreOffice 或 Chrome 等外部工具；缺少这些工具时命令会返回失败信息，不影响技能加载。

开发期用仓库根 `.agents/skills` 作为源：

```powershell
npm run sync:skills
```

该命令把 `pdf`、`pptx`、`docx` 同步到 `ling-vscode/resources/skills/`（生成物，已在 `.gitignore` 中忽略）；`vscode:prepublish` 会自动执行。

## 文档读取

`extract_document_text` 支持 PDF、DOCX、PPTX：

- `path` 可以是工作区相对路径，也可以是用户附件的绝对路径。
- `page_from` / `page_to` 对 PDF 和 PPTX 生效（从 1 开始，包含端点）。
- 输出 JSON，包含 `content`、`truncated`、`metadata` 和 `warnings`；PDF 用 `--- Page N ---`，PPTX 用 `--- Slide N ---` 分段。
- DOCX 会保留标题和表格；PPTX 按段落合并文本 run。
- `.doc`、`.ppt`、`.xlsx` 和图片 OCR 不支持；扫描版 PDF 只会给出警告。

PDF 解析依赖 `pypdf`：

```powershell
cd ling
python -m pip install -r requirements.txt
```

## 交互提问

`ask_user` 让模型在无法自行决定时向用户提问：

- 支持一次提交 1–4 个问题，每题 2–10 个选项；选项可以是字符串或 `{label, description}`。
- 支持单选、多选（`multi_select`）和自定义文本回答。
- Composer 区域会显示问题卡片；回答后作为该 tool call 的结果返回给模型。
- 问题本身不写入 SQLite；刷新 Webview 时通过 `session.snapshot.pendingQuestions` 恢复未回答的卡片，取消 run 后卡片消失。

PDF skill 的“样式偏好”选择就走这条链路。

## 执行中的消息

run 进行中仍然可以继续发送消息：

- 新消息进入 sidecar 内存队列（最多 20 条），当前 run 完成后按顺序自动发送。
- Composer 上方显示排队消息和 `Send now` 按钮；点击会中断当前 run，并立即发送该条消息。
- `Cancel run` 会同时清空队列。
- 队列不写 SQLite；刷新 Webview 通过 snapshot 的 `queuedMessages` 恢复，sidecar 重启后丢失。

## 上下文治理

Ling 在每次模型调用前检查上下文，避免长会话或大工具输出撑爆模型窗口：

- **大工具输出落盘**：超过 12,000 字符时写入 `.ling/spills/<session>/<call>.txt`，模型上下文只保留约 2,000 字符预览和路径；单条工具结果硬上限为 50,000 字符。
- **历史压缩**：默认按 1M 窗口计算，阈值约 848k。超过阈值时把较早的历史折叠成摘要，模型下一次只看到“摘要 + 最近完整消息”；默认保留最近 5 个用户轮，不是每个工具调用后都摘要。
- **完整历史保留**：SQLite 和 UI 仍保留完整用户/assistant/tool 消息，Timeline 只增加一条 `Context compacted · N messages folded` 标记。
- **失败策略**：摘要失败不会阻断对话；压缩事件持久化失败才会终止当前 run。

当前压缩参数在 `HarnessConfig` / `CompactionConfig` 中，尚未暴露到 Settings 面板。`.ling/spills/` 位于 workspace 内，建议在项目 `.gitignore` 中加入 `.ling/`。

## 构建

```powershell
npm run build
```

## 测试

```powershell
npm test
```

测试包含 Webview 类型检查、Timeline reducer、Webview transport、图片附件存储与路径校验、Ling 配置文件读写与迁移、Python JSON-RPC、sidecar 启动/退出/崩溃/重启集成测试和完整构建。

构建产物：

- Extension Host：`dist/extension.js`
- Webview：`media/webview/`
