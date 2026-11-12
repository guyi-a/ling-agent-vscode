# Changelog

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
