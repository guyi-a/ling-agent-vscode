# Changelog

## 2.0.0

- Add Windows computer use through UI Automation: `computer_list`, `computer_observe`, `computer_act`, and `computer_stop`, with a desktop-wide takeover approval, a blue control border, status-bar/banner Stop, and a global stop hotkey.
- Add `computer_launch`, which resolves Windows system paths/App Paths, launches an app, waits for its window, and returns the window id. This avoids Git/PATH shims and the command approvals that previously interrupted control.
- Add a helper fast path so `ping`, `control.end`, and `control.status` respond while UI Automation or input work is in flight; active actions abort at the next step when the session stops.
- Emit `helper_lost` when the helper exits unexpectedly and limit helper restarts to two per minute.
- Add UIA pattern actions plus `SendInput` fallbacks and readback for click, set value, type, key presses, focus, toggle, expand/collapse, selection, scrolling, and dragging.
- Add hard denies for terminals, password managers, UAC/secure desktop, the lock screen, elevated windows, and user-configured executables. Elevated windows are observe-only with redacted values.
- Coalesce parallel takeover approvals into one card and carry the grant through the first action, so later computer actions never prompt again in any mode.
- Computer tool cards now show the operation, window/app title, node/window counts, and failure codes instead of a bare window id; grouped desktop summaries also count launch, act, and stop calls.
- `computer_launch` now reuses an already-running app's existing window, including a window hidden to the tray, instead of starting a second instance. This prevents duplicate-instance login windows (for example WeChat) and avoids false login-state conclusions.
- Add `computer_focus`, a window-level restore/foreground action with the Win32 `AttachThreadInput` foreground fallback. It needs no UIA snapshot and is covered by the takeover approval.
- Add `computer_screenshot`: capture a window to PNG, save it locally, and return the image to the model. Read-only, no takeover approval; terminals, password managers, lock screens, and elevated windows are blocked. Screenshot images are not persisted in SQLite.
- Add coordinate actions to `computer_act`: `click_at`, `scroll_at`, `drag_at`, and `type` with x/y. Coordinates are bound to a screenshot's `capture_id` and converted to physical screen pixels; moved windows and expired captures return `STALE_CAPTURE`. This makes Qt/Electron/custom-drawn apps usable when the UIA tree is empty.
- Make `computer_observe` return one unified observation with a screenshot, UIA refs, focused element, and document text. `computer_act` now performs one action per observation and returns a fresh observation; no-effect validation errors preserve the takeover session.
- Preserve screenshot width across a `computer_act` refresh so consecutive coordinate observations keep the same scale, and expand the takeover overlay with a light outer halo, centered HUD, and physical-Esc stop.
- Type through the helper's clipboard without shell commands: text clipboard content is saved and restored, non-text content is replaced only when typing needs it and is reported back. `press_key` no longer requires a UIA ref, so Enter and Ctrl+V go to the focused control.
- End computer control automatically when an agent run finishes, so the border and banner disappear as soon as Ling is done and the user can take the desktop back without waiting for the TTL. Make the control border 5px at full opacity so the takeover state is obvious.
- Add the built-in `computer-use` skill with the UIA ref workflow, screenshot-coordinate recipe for Qt/Electron/custom-drawn apps, clipboard and key input rules, window launch/reuse behavior, error table, and control-session lifecycle.
- Resolve a bare executable name against already-running windows, so `computer_launch("Weixin.exe")` finds the installed path without a failed retry.
- Add control-session TTL/idle expiry, JSONL audit logs with input redaction and approval ids, explicit `UIPI_BLOCKED` detection, and Computer use settings.
- Package `comtypes` and the generated UIAutomation client into the sidecar.

## 1.2.2

- Fix the broken README logo in the extension details page by referencing the
  icon with an absolute URL.
- Connect model and embedding requests directly instead of through the system
  proxy, avoiding extra latency when a VPN is enabled.

## 1.2.1

- Show the Copy, Delete, and Regenerate actions only on the final assistant
  answer. Intermediate assistant paragraphs no longer show a lone Delete
  button in the middle of a run.
- Add an **Expand** action for inline visualizations. The diagram opens in a
  full-screen overlay with Escape/backdrop close, and the inline iframe now
  measures its content more accurately after load, resize, and streaming.
- Update the `ling-self` skill: MCP is a user-level global configuration,
  document the actual config/credential paths, and clarify skill precedence.

## 1.2.0

- Add background explore subagents. `spawn_subagent` accepts
  `run_in_background`; tasks are queued with configurable concurrency,
  persisted with the session, and can be stopped individually.
- Batch finished background tasks into one notification and one automatic
  continuation, even when several finish together.
- Add background task cards, a per-session Tasks panel with an active-task
  badge, and stop controls.
- Add manual backgrounding for running commands. A **Run in background**
  action on the tool card detaches the process without interrupting it,
  records a Command task with exit code and log path, and reuses the batched
  notification and automatic continuation flow.
- Add Command tasks to the Tasks panel with exit codes and **View log**.
  Stopping a command task terminates its process tree; full output is written
  to `.agents/background/<task-id>.log`.
- Bump the session database schema to v4 for command task metadata. Tasks
  interrupted by a sidecar restart are marked `interrupted` and reported when
  the session is reopened.
- Fix "allow and don't ask again" rules for compound commands whose
  subcommands start with quoted strings or executable paths.
- Keep the **Run in background** action outside the tool card as a compact
  bottom-left action, and show the run-level Copy action on answers produced
  by automatic background continuation.

## 1.1.1

- Add a single Copy action to the final answer's action row for each completed
  assistant response. It copies intermediate text, compact tool summaries, and
  the final answer in original order.

## 1.1.0

- Add a repository README and reposition Ling as a local agent harness for
  VS Code: tools, approvals, skills, and context without leaving the editor.
- Add the `ling-self` built-in skill describing Ling's product model,
  capabilities, storage, and overall release process.
- Require `read_file` before editing an existing file. Read state is tracked
  per session, updated after tool writes, and invalidated when the file
  changes on disk.
- Keep grouped tools at their original position when assistant text appears
  between calls, instead of collecting them across the whole run.
- Show the MCP management operation in tool cards (`add`, `test`,
  `authorize`, and so on) instead of repeating only the server name.
- Report an OAuth-required MCP connection test as `needs-auth` instead of a
  failed tool call.
- Rework the approval card with numbered keyboard options: allow once, allow
  edits for the session or remember a command in `.agents/settings.local.json`,
  deny, and deny with feedback for the model. Compound commands are split into
  per-subcommand allow rules, each of which must match.
- Use bundler module resolution in the extension typecheck config.

## 1.0.1

- Load user skills from `~/.agents/skills`, with builtin > global > workspace
  precedence; bundled skills can no longer be shadowed by a same-name skill.
- Mirror bundled skills into the global library with a `source: ling`
  marker. Updates compare a per-skill tree hash so locally modified copies are
  preserved.
- Parse YAML block scalar (`>-` / `|`) skill descriptions.
- Move MCP configuration to `%APPDATA%\Ling\mcp.json`, and MCP credentials and
  tool caches to `%LOCALAPPDATA%\ling-agent\`; the config is shared by all
  workspaces and no longer lives under the project `.agents` directory.
- Remove stale `.agents/notes` references from the documentation.

## 1.0.0

- Promote Ling to a stable release with a complete MCP client supporting
  stdio, Streamable HTTP, SSE fallback, cached tool manifests, and lazy,
  pooled server connections.
- Add OAuth 2.1 authorization-code flow with PKCE, metadata discovery,
  dynamic client registration, state validation, loopback callbacks,
  token refresh, and automatic reconnect.
- Store OAuth credentials with Windows DPAPI, support authorization and
  revocation from both Agent tools and Settings, and remove credentials
  together with deleted MCP server configurations.
- Add Agent-managed MCP configuration tools for listing, testing, adding,
  enabling, disabling, reloading, and removing servers. MCP capability
  changes always require approval, and configuration files are hot-reloaded.
- Add MCP-aware tool naming, risk effects, approval behavior, tool cards,
  connection status, diagnostics, and a Settings configuration entry point.
- Add per-run workspace change ledgers with net line counts, multi-file diff
  review, and conflict-safe undo for the current run.
- Fix repeated context compaction so cumulative message boundaries remain
  correct, and invalidate stale compaction state after history edits.
- Refine grouped tool cards, Skill cards, user-message presentation, and
  change-summary layout.

## 0.14.0

- Add direct browser control through the Kro Browser Bridge extension, with
  approval-aware navigation, page inspection, clicks, typing, and extraction.
- Add `web_search` and `web_fetch` tools backed by DeepSeek Search & Fetch.
  Web results use structured tool cards, reject private URLs, and are available
  in Plan mode as read-only operations.
- Group repeated completed tools by semantic category across a run. Exploration,
  edits, commands, browser actions, web calls, subagents, and workflow tools
  collapse into counted rows while the currently running tool stays visible.
- Keep a run expanded when it finishes in the current chat; completed tool
  groups collapse live, while the outer elapsed-time disclosure closes only
  when the user chooses. Historical runs still start collapsed.
- Add durable project memory under `.agents/memory/*.md`. Memories with
  `alwaysApply: true` are reloaded into the system prompt on every user turn;
  `AGENTS.md` remains the home for stable project facts and conventions.
- Add Memory tools and Settings management for listing, creating, editing,
  and deleting workspace or global memories. Conditional memories can use
  `globs`; duplicate names/content, parser errors, and size limits are surfaced
  as diagnostics.
- Consolidate workspace state under `.agents`: attachments, spills, code
  indexes, notes, plans, skills, and memories now share one directory with
  runtime-only subdirectories ignored by Git.
- Emphasize inline visualization in the system prompt, copy complete renderer
  code blocks, and keep trailing CJK prose outside auto-linked URLs.
- Add a stable bubble-sort utility and its focused test fixture.

## 0.13.0

- Add Plan as a fourth approval mode, alongside Manual, Edit automatically, and Auto; cycle modes with Shift+Tab.
- Plan mode explores read-only and writes a markdown plan under `.agents/plans/`. Plans appear as chat cards with Open source and Build actions.
- Build a plan from its card or the `Build plan…` CodeLens: choose the execution mode first, and edits made to the plan file are used as-is.
- Open the rendered plan preview automatically for the first plan created in a session; later plans stay in the chat card.
- Add a foreground explore subagent: `spawn_subagent` delegates read-only codebase research to an isolated agent and returns a concise summary.
- Show live subagent traces with compact tool cards, and keep the full trace available after completion.
- Improve tool routing guidance for `search_codebase`, grep, glob, and direct file reads.
- Restore the last conversation when the chat opens, with a boot screen while the sidecar reconnects.
- Color the composer border by approval mode.
- Fix Windows webview builds occasionally bundling two React copies, which could blank the chat.

## 0.12.0

- Add semantic code search: `search_codebase` now combines BM25 keyword
  ranking with embeddings through reciprocal rank fusion.
- Configure any OpenAI-compatible embedding provider in Settings; Alibaba
  Cloud DashScope and SiliconFlow work out of the box. Without a key Ling
  keeps using keyword-only search.
- Keep a local SQLite index in `.agents/code-index` with incremental updates:
  unchanged files are skipped and only changed chunks are re-embedded.
  Switching embedding models rebuilds the vectors.
- Add a Code index section in Settings with indexed-file progress, a live
  progress bar, and a build/refresh button.
- Show inline diffs for `edit_file` and `write_file` tool cards, with line
  numbers and added/removed counts; show IN/OUT sections for commands,
  searches, and reads.
- Merge all thinking from one run into a single Thoughts block.
- Raise the default max steps per message from 10 to 50.
- Keep Save and Cancel visible at the bottom of Settings; add show/hide for
  the embedding API key.
- Fix the sidecar losing embedding settings after a window reload.
- Rewrite the README for Marketplace readers.

## 0.11.0

- Generate Python docstrings from the editor with CodeLens or the context menu.
- Generate JSDoc for TypeScript and JavaScript, including arrow functions.
- Rewrite existing docstrings and JSDoc with the original comment in context.
- Preview generated comments in a diff before accepting; accepting saves the file.
- Skip empty assistant messages when a cancelled turn is followed by a new prompt.
- Serialize session commands so cancelling a run cannot race with the next message.
- Restore editor tabs after comment and edit diffs close.
- Move user message actions below the bubble and compact the thinking block.

## 0.10.0

Initial preview release.

- Coding agent backed by the Python `ling_rpc` sidecar.
- Streaming chat, tool cards, approvals, cancellation, and history search/delete.
- Image, file, editor, terminal, and chat-selection context.
- Native VS Code diff preview with inline view and editor-title approve/deny actions.
- Skills loaded from bundled resources and the workspace `.agents/skills`.
- `extract_document_text` for PDF, DOCX, and PPTX.
- `ask_user` interactive questions and queued follow-up messages.
- Inline `html type="renderer"` diagrams with streaming rendering, Copy, and Source.
