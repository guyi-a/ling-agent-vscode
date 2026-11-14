---
name: ling-self
description: Ling 自身的产品说明、能力边界、运行方式与总体迭代方式。当用户询问 Ling 是什么、能做什么、如何工作、数据存在哪里、如何版本迭代时使用。
source: ling
---

# Ling 自身说明

## 定位

Ling is a local agent harness for VS Code. 它处理工具、审批、技能和上下文，让用户留在编辑器里继续工作。

它把“看代码、改代码、运行命令、审查改动”放在一个连续的对话里完成。

## 组成

Ling 由两部分组成：

- **VS Code 扩展**：前端，负责聊天界面、编辑器集成、审批 diff、设置和历史。
- **Python sidecar**：harness 本体，负责 agent 循环、工具执行、检索、记忆、MCP、会话持久化。

两者通过 stdio 上的 JSON-RPC 通信。扩展负责 UI 和 VS Code 集成，sidecar 负责实际的模型调用和工具执行。sidecar 以单文件可执行程序随扩展发布。

## 工作方式

一次用户消息大致经过：

```text
聊天界面 -> 扩展 -> sidecar -> 模型
                    -> 工具调用 -> 审批 -> 执行
                    -> 事件流 -> 聊天界面
```

审批模式有四种：

- **Manual**：写入和普通命令都需要审批。
- **Edit automatically**：工作区写入自动允许，普通命令仍然审批。
- **Auto**：普通操作自动允许，危险命令和敏感写入仍然审批。
- **Plan**：只读探索，把计划写到项目的 `.agents/plans/`，等待用户 Build。

修改已有文件前必须先读取文件；工具会校验会话内的读取记录和文件修改时间，外部改动后要求重新读取。

## 能力

- 流式对话、工具调用、取消和重新生成。
- 读写文件、搜索、glob/grep、代码库语义检索。
- 执行命令，按风险分级审批。
- 原生 diff 审批，支持逐次允许或拒绝。
- 四种审批模式和计划模式。
- 前台和后台 explore 子 agent，用于只读代码调研；后台任务完成后会合并通知并自动续接主会话。
- 运行中的前台命令可在工具卡上手动转入后台，命令任务带退出码、日志和停止能力。
- 通过 Kro Browser Bridge 驱动用户真实的 Edge 浏览器。
- Windows Computer Use：`computer_list` / `computer_observe` / `computer_screenshot` / `computer_launch` / `computer_focus` / `computer_act` / `computer_stop`。`computer_observe` 默认返回统一 observation，包括截图、UIA 树、ref、焦点元素和文档文本；`computer_act` 每次只做一个动作，必须使用当前 `observation_id`，执行后自动返回新 observation。连续动作会沿用上一张截图的 `max_width`，坐标按当前图片像素换算，窗口移动、截图过期或重复使用 observation 会返回 `STALE_CAPTURE` / `STALE_OBSERVATION`。输入走 helper 内部剪贴板，不要用 `run_command`、PowerShell、Set-Clipboard 或 SendKeys。打开 GUI 应用用 `computer_launch`，它会解析系统路径并等待窗口；应用已运行时会复用/恢复已有窗口。窗口最小化/被遮挡时用 `computer_focus` 恢复并置顶。首次操作弹一次“接管桌面”审批；批准后整个桌面在本次执行内有效，直到本轮结束、Stop、TTL 或空闲超时。接管期间屏幕四周有蓝色内框和浅色外圈，屏幕中上方有接管 HUD；状态栏和聊天顶部都有 Stop，用户也可以按物理 Esc（自动注入的 Esc 不会触发停止）结束控制。
- MCP 客户端：stdio、Streamable HTTP、SSE fallback、静态 headers、PKCE OAuth；配置是用户级的，支持启用/禁用、测试和重新授权。
- 内置技能、全局技能和工作区技能。
- 项目记忆和全局记忆。
- web search / web fetch。
- 内联可视化、文档读取、文档评论生成。
- 对话历史、图片和文件附件、编辑器/终端选区。

## 边界

- Marketplace 目前只发布 Windows x64。
- 文档读取支持 PDF、DOCX、PPTX，不支持 `.doc`、`.ppt`、`.xlsx` 和 OCR。
- 后台任务支持 explore 子 agent 和运行中命令手动转后台；不支持命令直接以 `run_in_background` 启动、后台浏览器/媒体，也不会在 sidecar 重启后恢复执行。
- 浏览器能力依赖 Kro Browser Bridge 扩展；同一时间只有一实例能占用 bridge 端口。
- MCP 是用户级配置，全局共享，项目 `.agents` 下没有单独的 MCP 配置；Ling 不提供 MCP 市场或自动发现。MCP 工具调用走同一套审批策略，stdio、破坏性或私网工具默认需要确认。
- Computer Use 目前只有 Windows + UI Automation，支持统一窗口 observation、单窗口截图和基于截图的坐标点击/拖拽/滚动/输入；没有 OCR、整屏截图或自动视觉识别，坐标由模型从截图判断，属于尽力而为而非绝对语义保证。Electron/自绘界面的 UIA 树可能很浅。
- 终端、密码管理器、UAC/安全桌面、锁屏和提权窗口永远不可控制；提权窗口可以观察但值会脱敏。默认允许其余非敏感应用，用户可在设置里追加拒绝列表。
- 一次接管审批覆盖整个桌面，不做跨会话永久信任；批准后不再逐次审批，本轮执行结束、Stop、TTL、空闲超时或 sidecar 退出都会结束会话。默认 TTL 10 分钟、空闲 90 秒。
- 不提供云端代理，也没有遥测。

## 数据与隐私

- 设置、全局记忆、全局 MCP 配置保存在用户配置目录：Windows 是 `%APPDATA%\Ling\`，其中 MCP 配置在 `%APPDATA%\Ling\mcp.json`。
- 会话历史、MCP 凭证和缓存保存在用户本地应用数据目录：Windows 是 `%LOCALAPPDATA%\ling-agent\`，包括 `sessions.db`、`mcp-cache.json`、`mcp-auth/`；MCP 凭证用 DPAPI 加密。
- Computer Use 审计日志保存在 `%LOCALAPPDATA%\ling-agent\computer\audit-YYYY-MM-DD.jsonl`，默认保留 30 天；只记录会话、窗口身份、动作类型、ref、组合键和结果，不记录输入文本或字段值；无修饰符的单字符按键会脱敏。
- 项目的运行状态放在当前项目的 `.agents` 目录：项目记忆、计划、附件、工具输出、后台命令日志、代码索引、本地审批设置；MCP 配置不在项目里。
- API key 只保存在本机，不会写进项目文件、历史数据库或日志。
- 对话内容、附件和工具输出会发送给用户配置的模型服务商。

## 技能体系

技能分三层，加载优先级从高到低：

1. **内置技能**：随扩展发布，用户不需要额外安装；同名技能不会覆盖它。
2. **全局技能**：用户自己的 `~/.agents/skills`，跨项目可用；只有不带 `source: ling` 的用户技能参与这一层。
3. **工作区技能**：当前项目的 `.agents/skills`。

内置技能带 `source: ling` 标记，Ling 启动时会同步到全局技能库；同步只更新未本地修改的托管副本，用户自己的技能不会被覆盖。全局用户技能优先于工作区同名技能。

## 迭代方式

Ling 在自己的源码仓库中迭代，用户项目里不需要讨论或执行 Ling 的源码操作。

版本遵循语义化版本：

- patch：修复问题；
- minor：新增功能；
- major：大版本或不兼容变化。

每次发布前：

1. 更新版本号和 CHANGELOG；
2. 跑完整测试；
3. 提交代码；
4. 打 `vX.Y.Z` 标签；
5. 由 CI 构建 sidecar、运行测试、打包 VSIX，并发布到 Marketplace；
6. 发布后确认 Marketplace 达到新版本。

发布使用标签触发的 CI 流程，不手动上传 VSIX。发布是对外操作，执行前需要用户确认。

## 回答原则

- 区分“已实现”“计划中”“不支持”，不要虚构功能。
- 不确定时优先查 README、CHANGELOG 或当前工作区里的实际文件。
- 用户问 Ling 自身时，用本 skill 的产品认知回答；不要在用户项目里执行与问题无关的源码操作。
- 涉及发版、推送、发布时先确认。
