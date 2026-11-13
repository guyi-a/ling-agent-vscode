<p align="center">
  <img src="media/icon.png" width="72" alt="Ling" />
</p>

<h1 align="center">Ling</h1>

<p align="center"><strong>Harness your workflow without leaving VS Code.</strong></p>

Ling is a local agent harness for VS Code. It handles tools, approvals, skills, and context, so you can stay in your editor and keep moving.

It can read and search your code, edit files, run commands, read PDF/DOCX/PPTX, and generate project-style comments. Writes and normal commands require approval by default; file edits are shown as native VS Code diffs.

Requires **Windows x64** and **VS Code 1.96+**.

## Features

- **Chat and tools**: streaming replies, tool cards, file reads and edits, search, and command execution.
- **Approvals and diffs**: native diffs before writes; normal commands and sensitive operations require approval by default.
- **Plan mode**: read-only exploration that writes an implementation plan to `.agents/plans/`. Open the plan in the editor, then use the `Build plan…` CodeLens or the Build button on the chat card.
- **Comment generation**: click `Generate comment` above a function, method, or class to generate a Python docstring or TS/JS JSDoc. Existing comments are included so the model can keep what is correct and fill in what is missing.
- **Context**: editor and terminal selections, images, files, PDF/DOCX/PPTX.
- **Skills**: built-in `pdf`, `pptx`, `docx`, `inline-visualization`, `browser-bridge`, `frontend-design`, and `ling-self`, plus user-global `~/.agents/skills` and workspace skills. Built-ins take precedence.
- **Questions**: when the model needs a decision, it shows an interactive question card.
- **History**: isolated per workspace, with search, delete, single-message delete, and regenerate; the last conversation is restored when the panel reopens.
- **Works without a folder**: with no folder open, Ling uses a fixed scratch workspace.
- **Inline visualization**: `html type="renderer"` code blocks are rendered as diagrams in the chat.

## Install and configure

1. Install **Ling** from the VS Code Marketplace.
2. Click the Ling icon in the activity bar to open Chat, or run `Ling: Open Chat` from the command palette.
3. Click the gear icon in the panel and set **API Key**, **Model**, and **API Base URL**, then save.

The default model is `deepseek-chat` and the default base URL is `https://api.deepseek.com/v1`. Any OpenAI Chat Completions-compatible endpoint works.

Settings are stored in Ling's own file:

- Windows: `%APPDATA%\Ling\config.json`

The API key is stored there in plain text and is never written to project files, the history database, or logs.

## Comment generation

Place the cursor in a function, method, or class. `Generate comment` appears above the function; you can also right-click and choose `Generate Comment`, or run `Ling: Generate Comment` from the command palette.

Supported languages:

- **Python**: Google-style docstrings.
- **TypeScript / JavaScript**: JSDoc with `@param` and `@returns`.
- **TSX / JSX**: supported.
- **Arrow functions**: `const foo = (a: number) => ...` is supported.

A diff preview opens after generation:

- Accept: `Ctrl+Alt+Enter`, or click `✓` in the diff title bar.
- Reject: `Ctrl+Alt+Backspace`, or click `✕`.
- Accepting writes the comment and **saves the file**; rejecting leaves the file untouched.

If a function already has a docstring or JSDoc, Ling sends the original comment to the model, asks it to preserve correct content and fill gaps, then replaces the original with the new comment.

Limitations:

- Python, TypeScript, JavaScript, TSX, and JSX only.
- The file must already be saved to disk.
- Python single-line function bodies (`def f(): return 1`) are not supported.
- Python falls back to text scanning without a language server; TS/JS uses VS Code's built-in TypeScript language service.

## Chat and tools

Describe what you need in Chat, and Ling decides which tools to call:

- read files and directories;
- search text;
- write or edit files;
- run commands;
- read PDF, DOCX, and PPTX;
- load skills;
- ask you questions.

Tool calls appear as cards that expand to show arguments and results. While a run is active, click **Stop** to the right of the composer; you can send the next message immediately after stopping.

### Explore subagent

For research tasks that would otherwise flood the conversation with intermediate results, Ling delegates to a read-only explore subagent:

- The subagent can only read, search, and browse files; it cannot edit files, run commands, or ask questions.
- It uses a separate context and returns only a summary to the main conversation.
- A **Subagent** card shows live tool calls and intermediate output while it runs.
- When it finishes, the card shows the summary; click **View trace** to review the full execution trace.

The main conversation continues only after the subagent finishes, so this fits tasks that need a research result immediately.

### Browser (Kro Bridge)

Ling can drive the Edge browser you are already using. Install the **Kro Browser Bridge** extension in Edge first; Ling's sidecar starts a local bridge and does not go through MCP.

- The bridge starts listening on `127.0.0.1:17323` only on the first `browser_bridge` call.
- In the extension, choose Port Auto detect or enter `17323`, then click Reconnect until Connected.
- Only one bridge can own port 17323 at a time. If Claude Code's `kro-bridge` MCP is also running, stop or remove it first.
- The model checks `extension_status` first. If the extension is not connected, it stops and asks you to fix it; it does not silently fall back to another browser.
- Clicking, typing, and opening/closing tabs require approval; `execute_script` always requires approval.
- Plan mode allows only read-only actions such as `extension_status`, `list_pages`, `read_state`, and `extract`.

### MCP

MCP servers are user-level configuration shared by all workspaces:

- Config: `%APPDATA%\Ling\mcp.json`
- OAuth credentials: `%LOCALAPPDATA%\ling-agent\mcp-auth\`
- Tool manifest cache: `%LOCALAPPDATA%\ling-agent\mcp-cache.json`

Stdio, Streamable HTTP, SSE fallback, static headers, and PKCE OAuth are supported. Windows credentials are encrypted with DPAPI. Settings → MCP can open the config file, enable or disable servers, and re-authorize.

### Approval modes

Switch modes from the mode button at the bottom-right of the composer, or press **Shift+Tab** to cycle:

- **Manual**: writes and normal commands require approval.
- **Edit automatically**: workspace writes are allowed; normal commands still require approval.
- **Auto**: normal operations are allowed; dangerous commands and sensitive paths/content still require approval.
- **Plan**: read-only exploration; no file edits or commands. The model writes a plan to `.agents/plans/` with `create_plan`, then waits for Build.

In Plan mode, a `Build plan…` CodeLens appears at the top of the plan file, and the plan card in chat also has a Build button. Build first asks for Manual / Edit automatically / Auto, then reads the current file and executes it; edits made in the editor are used as-is.

Approval mode is saved per session; a new session starts in Manual.

### Approval card

You can use the mouse or the number keys:

- `1`: Yes, allow once.
- `2`: For edits/writes, “allow all edits this session” switches to Edit automatically. For commands, “don't ask again” saves command rules. For compound commands, one rule is saved per subcommand.
- `3`: No.
- `4`: Type a rejection reason. Press Enter to deny and return the text to the model as a tool error.
- `Esc`: Cancel the run.

“Don't ask again” writes to the project's `.agents/settings.local.json` (gitignored):

```json
{
  "permissions": {
    "allowCommands": ["npm:*", "git status"]
  }
}
```

`npm:*` is a prefix match. Dangerous commands never use the allowlist and always require approval.

Compound commands are split on `&&`, `||`, `;`, `|`, `|&`, `&`, and newlines. Every subcommand must match the allowlist before the command runs without a prompt. Selecting `2` saves a separate rule for each subcommand, up to five. Commands containing `$`, backticks, redirection, newlines, or unparseable syntax still offer only 1/3/4.

### Reasoning effort

The same mode menu offers five effort levels:

```text
Low · Medium · High · Extra high · Max
```

The default is **High**. Higher effort spends more reasoning tokens. The choice is saved in Ling's settings and sent with every message; no restart is required.

### File edit diffs

When `write_file` or `edit_file` waits for approval, VS Code opens a native diff:

- The left side is the file on disk; the right side is the proposed content. New files have an empty left side.
- Inline View (single-column red/green) is the default. Control it with the VS Code setting `ling.diff.inlineView`; turn it off for side-by-side.
- Press `1` to allow or `3` to deny in the approval card, or click `✓` / `✕` in the diff title bar.
- Files over 1 MB, binary content, and missing `old_text` do not show a diff, but approval still applies.
- An existing file must be read with `read_file` first. One read per session is enough for repeated `edit_file` calls; external changes require reading again.
- Changes made by `run_command` do not show a diff.

### Message actions

- Hover over user messages and model replies to copy or delete them; deletion requires confirmation.
- The latest model reply can be regenerated.
- Messages cannot be deleted or regenerated while a run is active.

## Context

### Editor and terminal selections

- **Editor**: select code and click `Add N lines to Ling chat` on the first selected line, or right-click `Add Editor Selection to Ling Chat`.
- **Terminal**: select terminal text and right-click `Add Terminal Selection to Ling Chat`.
- **Chat**: select message text to `Copy` or `Quote` it into the composer.

Each message supports up to four selections, each up to 50,000 characters. Editor selections in history can be clicked to return to the original location.

### Images and files

Use `+` at the bottom-left of the composer to pick files, or paste/drag images directly.

- Up to four attachments per message (images and files combined).
- Images: PNG, JPEG, WebP, GIF, BMP; up to 4 MB each.
- Images are copied to the workspace `.agents/attachments/`; messages store references only.
- Other files are not copied; each file can be up to 25 MB and is read by tools when needed.
- Image-only messages are supported.

Add `.agents/attachments/`, `.agents/spills/`, and `.agents/code-index/` to your project `.gitignore`; `.agents/memory/` and `.agents/skills/` can be committed.

### Document reading

`extract_document_text` supports PDF, DOCX, and PPTX:

- Workspace-relative and absolute paths are supported.
- `page_from` / `page_to` apply to PDF and PPTX (1-based, inclusive).
- Output includes text, truncation status, page count, and warnings; PDF uses `--- Page N ---` and PPTX uses `--- Slide N ---`.
- DOCX preserves headings and tables; PPTX merges text by paragraph.
- Up to 25 MB per file.
- `.doc`, `.ppt`, `.xlsx`, and image OCR are not supported; scanned PDFs report a missing text layer.

## Skills

Ling loads skills from three locations, highest priority first:

- Built-in: `<extension>/resources/skills/<name>/SKILL.md`
- Global: `~/.agents/skills/<name>/SKILL.md` without `source: ling`
- Workspace: `<workspace>/.agents/skills/<name>/SKILL.md`

Built-ins take precedence; a same-name global or workspace skill cannot override them. On startup, Ling mirrors built-ins into the global library and marks them with `source: ling`. Upgrades only replace managed copies that have not been modified locally, using per-skill hashes; unmarked directories are never touched. To customize a built-in, copy it and use a different `name`.

Ling puts only a skill's `name` and `description` into context. When the model decides to use one, it calls `load_skill` for the full body, then uses `skill_path` / `scripts_path` for scripts.

Built-in skills: `pdf`, `pptx`, `docx`, `inline-visualization`, `browser-bridge`, `frontend-design`, `ling-self`.

PDF/PPTX/DOCX generation and conversion scripts depend on external tools such as `uv`, pandoc, LibreOffice, and Chrome. Missing tools produce clear command errors without affecting skill loading.

## Interactive questions

When the model needs a decision, a question card appears above the composer:

- Up to four questions per request, each with up to ten options.
- Single-select, multi-select, and custom text answers are supported.
- Answers are returned to the model as tool results.

## Messages during a run

You can keep typing while a task runs:

- New messages are queued (up to 20) and sent in order after the current task.
- Queued messages appear above the composer; click `Send now` to interrupt the current task and send immediately.
- `Stop` or `Cancel run` clears the queue.
- The queue is in memory only and is lost when Ling restarts.

## History

Click the history button at the top of Chat to view sessions for the current workspace:

- Search by title, message text, and attachment name.
- Delete sessions with an inline confirmation.
- Opening history restores user messages, model replies, tool cards, and approvals; sending again continues the same session.
- Sessions are isolated per workspace and do not appear in other projects.

## No-folder workspace

With no folder open, Ling uses a fixed workspace:

- Windows: `%LOCALAPPDATA%\ling-agent\scratch`

Chat, attachments, history, file tools, and command tools all work there; Chat shows `No folder` at the top. Without a folder, edit diffs do not open automatically, but approvals still appear in Ling's card.

## Context management

Long conversations do not keep full tool output in the model window forever:

- Tool results over 12,000 characters are written to `.agents/spills/<session>/<call>.txt`; the model sees about 2,000 characters of preview plus the file path. A single result is capped at 50,000 characters.
- Near the context limit, older history is folded into a summary; the model sees the summary plus recent complete messages. The last five user turns are kept by default.
- Full history remains in the local database and UI; chat adds only a `Context compacted · N messages folded` marker.
- The context window and kept-turn count are configurable in Settings.

## Inline visualization

When the model emits a block like this, Ling renders a diagram instead of source:

````text
```html type="renderer"
<svg ...>...</svg>
```
````

- Updates roughly every 70 ms while streaming.
- Rendered in a sandboxed iframe; scripts and external resources are blocked.
- Height follows content; width follows the message area.
- `Copy` (source) and `Source` (expand source) are available at the top-right.

Only `html` blocks with `type="renderer"` are rendered; normal code blocks are unchanged.

## Settings

Click the gear at the top-right of Chat to open Settings:

| Setting | Default | Description |
|---|---|---|
| API Key | empty | Saving an empty value deletes the stored key |
| Model | `deepseek-chat` | Model name |
| API Base URL | `https://api.deepseek.com/v1` | OpenAI-compatible endpoint |
| Reasoning effort | `High` | Low / Medium / High / Extra high / Max |
| Diff view | Inline | Or Side by side |
| Max steps per message | 50 | 1–100 |
| Parallel tools | 4 | 1–16 |
| Command timeout | 120s | 1–3600 |
| Approval timeout | 120s | 1–3600 |
| Context window | 1,000,000 | 1024–10,000,000 |
| Keep recent user turns | 5 | 0–50 |
| Embedding model | `text-embedding-v4` | Change when switching providers |
| Embedding base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Change for another compatible service |
| Embedding API Key | empty | Empty reuses the main API key |
| Skills | — | Read-only list with source and path |

Saving restarts Ling's background process; changes apply to the next request. Settings cannot be opened while a run is active.

Semantic search is optional. DeepSeek does not provide embeddings, so configure a separate supported service, for example:

- Alibaba Cloud DashScope: base URL `https://dashscope.aliyuncs.com/compatible-mode/v1`, model `text-embedding-v4`
- SiliconFlow: base URL `https://api.siliconflow.cn/v1`, model `BAAI/bge-m3`

With embeddings configured, `search_codebase` takes BM25 and vector candidates and fuses them with RRF. Changed models, documents, or indexes are updated incrementally per chunk, so editing one function does not re-embed the whole repository. Without embeddings or on failure, it falls back to keyword-only search. Switching embedding models clears old vectors and rebuilds.

The first search builds the vector index synchronously: a few hundred chunks take tens of seconds, then updates are incremental. Tool results report `mode` as `hybrid` or `bm25`.

**Code index** at the bottom of Settings can also be triggered manually (`Ling: Build Code Index` in the command palette). It uses the same incremental logic: unchanged mtime/size skips a file; changed files re-embed only chunks whose `content_hash` changed, preserving vectors for other functions. Without embeddings it builds a keyword-only index.

## Privacy and security

- The API key is stored in plain text in `%APPDATA%\Ling\config.json`; it is not written to project files, the history database, or logs.
- Model requests go directly to the API endpoint you configure. Conversation content, attachments, tool output, and workspace context may be sent to that provider.
- With semantic search enabled, code chunk text is sent to your embedding service when indexing; without it, only local keyword search is used.
- Ling has no cloud proxy and no telemetry.
- Writes outside the workspace are blocked by default; `write_file` / `edit_file` only modify paths inside the workspace.
- `run_command` can execute arbitrary PowerShell commands, subject to approval mode; dangerous commands and sensitive operations always require approval.
- Image attachments are copied to the project's `.agents/attachments/`; conversation history is stored in `%LOCALAPPDATA%\ling-agent\sessions.db`.

## Known limitations

- The Marketplace build currently supports **Windows x64** only.
- Comment generation supports Python, TypeScript, JavaScript, TSX, and JSX only; Python single-line function bodies are not supported.
- Document reading has no OCR and does not support `.doc`, `.ppt`, or `.xlsx`.
- PDF/PPTX/DOCX generation scripts require external tools.
- File changes made by `run_command` do not show a diff.
- The in-run message queue is in memory only and is lost on restart.
- Some internal compaction parameters are not exposed in Settings.

## Feedback

Report issues or request features on GitHub:

https://github.com/guyi-a/ling-agent-vscode/issues

## Development

```powershell
cd ling-vscode
npm install
npm run build
npm test
```

Build the Python runtime:

```powershell
npm run build:sidecar
```

Open the `ling-vscode` directory in VS Code and press `F5` to start the Extension Development Host.

## License

MIT
