---
name: browser-bridge
description: 用用户真实 Edge 浏览器完成网页任务。任何浏览器场景先调用 browser_bridge(action='extension_status')；ready=true 才继续，ready=false 就停下来让用户打开 Kro Browser Bridge 扩展并连接。适用工具：browser_bridge。
source: ling
---

# Skill: browser-bridge

驱动用户**自己的** Edge 浏览器。它复用用户已有的登录态，用户也能看到 agent 正在操作哪个页面。

## 决策规则

1. 任何浏览器任务的第一步都是 `browser_bridge(action='extension_status')`
2. `ready=true` → 用 bridge 完成整个任务
3. `ready=false` → **停止**，告诉用户 Kro Browser Bridge 扩展没有连上，请打开扩展并点 Reconnect
4. 不要问用户“要不要装扩展”，不要循环重试 `extension_status`，也不要假设有其它浏览器工具

## 工作流

1. **探测**：`extension_status`，确认 `ready`
2. **拿 browser_id**：`list_sessions`，通常只有一个
3. **看已有 tab**：`list_pages`；用户可能已经打开了目标页面，直接 `focus_page` 复用
4. **新开 tab**：`open_tab(browser_id, url)`，记住返回的 `page_id`
5. **观察**：`read_state(browser_id, page_id)`，拿到编号元素列表
6. **操作**：按 index 调 `click` / `hover` / `dblclick` / `rightclick` / `type` / `press`
7. **验证**：每次交互后重新 `read_state`，看 URL、标题或 DOM 是否变化
8. **收尾**：不要主动 `close_tab`，用户可能还要用这个页面

## 硬性纪律

### 一 · 操作成功 ≠ 业务成功

登录、提交表单、下单之后必须再 `read_state`，看到明确证据（跳转 URL、成功提示、订单号、用户头像）才能说完成。

### 二 · 用户阻断立刻交回

登录密码、CAPTCHA、短信/邮箱验证、2FA、支付、实名、“我同意”条款——全部停下来，把当前 URL、阻断原因和需要用户做什么告诉用户。

### 三 · index 只在下一次 read_state 前有效

编号是 DOM 快照。页面一变就全部失效；每次操作前重新读。

### 四 · page_id 会过期

用户手动关 tab、扩展重连后，旧 page_id 都可能失效。报 `page_id not found` 时不要重试同一个 id，重新 `list_pages`。

### 五 · 这是用户的真实浏览器

- 不要关用户没让你关的 tab
- 不要导航到任务无关的页面
- 不要执行破坏性 JS
- 不要在用户已登录的敏感站点做超出请求范围的操作

## Action 速查

| Action | 参数 | 用途 |
|---|---|---|
| `extension_status` | — | 探测扩展是否连接 |
| `list_sessions` | — | 拿 browser_id |
| `list_pages` | browser_id | 列 tab，拿 page_id |
| `open_tab` | browser_id, url, active? | 新开 tab |
| `focus_page` | browser_id, page_id | 切到指定 tab |
| `close_tab` | browser_id, page_id | 关 tab（谨慎） |
| `read_state` | browser_id, page_id | 拿可交互元素编号 |
| `click` / `hover` / `dblclick` / `rightclick` | browser_id, page_id, index | 点击变体 |
| `type` | browser_id, page_id, index, text | 填输入框 |
| `press` | browser_id, page_id, key, index? | 键盘按键 |
| `scroll` | browser_id, page_id, x, y, index? | 滚动 |
| `wait_for` | browser_id, page_id, timeout_ms? | 等页面稳定 |
| `go_back` / `reload` | browser_id, page_id | 导航 |
| `extract` | browser_id, page_id, index, include_html? | 取元素文本/HTML |
| `describe_element` | browser_id, page_id, index | 拿稳定 selector |
| `execute_script` | browser_id, page_id, script | 执行 JS（需审批） |

## execute_script 约束

扩展对换行和双引号很敏感，脚本必须：

1. **单行**，不能有换行或缩进
2. 用 **IIFE + 显式 return**：`(function(){...return x;})()`
3. 字符串全部用单引号；对象 key 不加引号
4. 返回体控制在 KB 级，必要时 `slice(0, N)`
5. 返回 `null` 先 `wait_for` 重试一次；仍为 null 就停止并报告

## 失败处理

| 现象 | 措施 |
|---|---|
| `ready=false` | 让用户连接扩展，停止本任务 |
| `browser_id not connected` | 扩展掉线；让用户检查 Edge 和扩展后重试 |
| `page_id not found` | 重新 `list_pages` |
| `timed out` | 不要盲重试；先 `read_state` 看页面状态 |
| index 越界 | DOM 变了，重新 `read_state` |
