---
name: computer-use
description: Windows 桌面控制操作手册。需要打开、观察或操作原生应用时使用，尤其是 Qt/Electron/自绘界面、UIA 树很浅、需要按截图坐标点击或输入文字的场景。适用工具：computer_list、computer_observe、computer_screenshot、computer_focus、computer_launch、computer_act、computer_stop。
source: ling
---

# Skill: computer-use

原生应用优先用 UI Automation；UIA 看不到控件时，用截图 + 坐标。浏览器里的网页走 browser_bridge，不要用桌面包一层去点。

## 决策顺序

1. `computer_list` 找到目标窗口；需要时用 `query` 过滤。
2. 窗口最小化、在托盘或被遮挡：`computer_focus`。
3. 应用没开：`computer_launch`；已在运行会复用已有窗口，不要重复启动。
4. 先 `computer_observe` 获取统一窗口状态：
   - 默认同时返回截图、`observation_id`、UIA 树、焦点元素和文档文本。
   - 有可操作控件 → 用 ref 动作。
   - 只有 1 个空壳节点、树很浅、元素没有名字 → 用截图坐标。
5. `computer_act` 每次只做一个动作，必须带当前 `observation_id`。
6. 动作成功后 `computer_act` 会返回新的观察状态；不要复用旧 observation、ref、坐标或截图。

## 硬性纪律

- 不要用 `run_command`、PowerShell、`Set-Clipboard`、`Get-Clipboard`、`SendKeys`、Add-Type 或 Win32 窗口脚本来做桌面操作；一律走 computer_* 工具。
- 密码字段禁止 `set_value` / `type`；不要绕过，让用户自己输入。
- 结果里出现 `mayHaveTakenEffect` 时，先 `observe` 或截图确认，再决定是否重试；不要自动重复有状态的动作。
- 窗口最小化时先 `computer_focus`，恢复后再 `observe` / 截图。
- 应用在运行但看不到窗口时，用 `computer_list(include_all=true, query=...)` 找托盘/隐藏窗口，再 `computer_launch` 或 `computer_focus`；不要用 `Get-Process`、`tasklist`、`Win32_Process`。
- `computer_launch` 复用已有实例后，不要从第二个窗口推断登录/账号状态；比较 PID，以原实例为准，并说明另一个窗口是独立实例。
- `foreground` 为 false 或返回 `FOCUS_BLOCKED` 时，请用户点一下窗口或从托盘打开，不要盲重试。
- `computer_act` 必须带 `computer_observe` 返回的 `observation_id`，并且每次只提交一个动作。
- ref 和坐标都来自同一 observation；动作后立即使用返回的新 observation。
- UIA 树很浅的 Electron 应用，直接把限制说清楚，不要猜控件。

## UIA 流程

- `computer_observe(window_id)` 返回 `observation_id`、截图、聚焦元素、文档文本和 `@snap:eN` ref。
- `computer_act` 的每个动作必须使用当前 `observation_id`；旧 observation/ref 会返回 `STALE_OBSERVATION` / `STALE_REF`。
- 页面变了就重新 observe；不要在没有新 observation 时继续猜着操作。
- 动作后的自动 observation 会沿用上一张截图的 `max_width`；坐标仍只按当前返回图片的像素计算。
- UIA 的 `type` 会优先粘贴；结果里的 `verified` 为 false 时要用截图确认。
- `press_key` 可以不带 ref，直接发给当前焦点控件。

## 截图坐标流程（Qt / Electron / 自绘界面）

1. `computer_observe(window_id, max_width)`，记住返回的 `observation_id`、`width`、`height`。
2. 在图片上确定坐标，坐标原点就是图片左上角，单位是图片像素。
3. `computer_act` 传入同一个 `observation_id`，并且只做一个动作：
   - `{"type":"click_at","x":640,"y":300}`：左键单击；`button` 可选 left/right/middle，`clicks` 1–3。
   - `{"type":"type","x":640,"y":300,"text":"你好"}`：先点坐标，再输入。
   - `{"type":"scroll_at","x":640,"y":400,"direction":"down","amount":3}`。
   - `{"type":"drag_at","x":100,"y":100,"to_x":500,"to_y":300}`。
   - `{"type":"press_key","key":"enter"}`：发给当前焦点控件。
4. 动作改变布局、打开弹窗、切换页面后，**重新 observe**；动作返回的新 observation 可以直接用于下一步。
5. `STALE_OBSERVATION` / `STALE_CAPTURE` 表示状态已过期；重新 observe，不要原坐标重试。
6. 坐标动作会自动把目标窗口切到前台；`FOCUS_BLOCKED` 时请用户点一下窗口，不要盲重试。

示例：在 Qt 聊天应用里发送一句话

```text
computer_observe(window_id) -> observation_id + screenshot
computer_act(window_id, observation_id, actions=[
  {"type":"click_at","x":...,"y":...},
])
computer_act(window_id, observation_id, actions=[
  {"type":"type","x":...,"y":...,"text":"要发送的内容"},
])
computer_act(window_id, observation_id, actions=[
  {"type":"press_key","key":"s","modifiers":["ctrl"]},
])
```

每次调用只做一个动作；上面示意的是连续三轮“动作 → 使用返回的新 observation”。

## 输入与剪贴板

- `type` 内部用 helper 的剪贴板粘贴，会在结束后恢复文本剪贴板。
- 结果里 `clipboardReplaced: true` 表示原剪贴板是非文本内容，已被替换且无法恢复；结束任务时要告诉用户。
- 不要用 `run_command`、PowerShell、`Set-Clipboard`、`Get-Clipboard`、`SendKeys` 或 Add-Type 做输入/剪贴板/窗口操作。命令审批会打断控制，且会抢焦点。
- 密码字段禁止 `set_value` / `type`；不要绕过，让用户自己输入。
- `press_key` 推荐写 `key:"s", modifiers:["ctrl"]`；也接受 `key:"ctrl+s"` 简写。

## 文档编辑与保存

1. 修改文本后先读取新 observation 的文档文本，确认内容和用户目标一致。
2. 保存使用一个独立动作：`{"type":"press_key","key":"s","modifiers":["ctrl"]}`。
3. 保存后必须使用动作返回的新 observation 再确认内容；不能只凭 `ok:true` 宣称保存成功。
4. 文件在当前工作区内时，可用 `read_file` 核对磁盘内容。
5. 文件在工作区外时，请用户确认；不要用 `run_command` 绕过文件访问边界。

## 窗口与应用

- `computer_launch("Weixin.exe")` 会解析系统路径，优先复用已运行实例的窗口，包括托盘里的隐藏窗口。
- 如果返回提示窗口仍在托盘：让用户从托盘打开，不要启动第二个实例。
- 不要用 `Get-Process`、`tasklist`、`Win32_Process` 查窗口；用 `computer_list`。
- 窗口标题或尺寸变了可能意味着换了页面；重新观察/截图。

## 控制会话

- 第一次 `computer_act` / `computer_focus` / `computer_launch` 弹一次“接管桌面”审批；批准后本次执行内不再逐次审批。
- 本次 agent 执行结束后 control 自动断开，蓝框和 banner 消失；下一轮任务会重新审批。
- 任何时候可以 `computer_stop`；用户也可以点 Stop、按物理 `Esc`，或按 `Ctrl+Alt+Shift+Backspace`。
- 接管期间屏幕四周有蓝色内框和浅色外圈，屏幕中上方显示接管 HUD；自动注入的 Esc 不会触发停止。

## 错误对照

| 错误 | 含义 | 处理 |
|---|---|---|
| `STALE_CAPTURE` | 截图过期或窗口移动/缩放 | 重新截图 |
| `STALE_OBSERVATION` | 观察已使用或过期 | 重新 observe |
| `STALE_SNAPSHOT` / `STALE_REF` | UIA 快照/元素过期 | 重新 observe |
| `WINDOW_MINIMIZED` | 窗口最小化 | `computer_focus` 后重试 |
| `FOCUS_BLOCKED` | 系统拒绝切前台 | 请用户点一下窗口 |
| `WINDOW_CHANGED` | 窗口换了进程 | 重新 list/observe |
| `UIPI_BLOCKED` | 目标权限更高 | 不要重试，请用户处理 |
| `APP_BLOCKED` | 终端/密码管理器/安全桌面等 | 停下并说明 |
| `HELPER_ERROR` | helper 异常 | 重新 list；必要时重开目标应用 |
| `INVALID_ARGS` | 参数在发送前被拒绝 | 修正参数；不要重试同一动作 |

## 边界

- 终端、密码管理器、UAC/安全桌面、锁屏永远不可控制。
- 提权窗口只能观察，值会脱敏，不能操作。
- 没有 OCR；截图内容由模型自己看。
- 不要跨用户轮次假设 control 还在；每轮结束时它已经断开。
