# Changelog

## 0.12.0

- Add semantic code search: `search_codebase` now combines BM25 keyword
  ranking with embeddings through reciprocal rank fusion.
- Configure any OpenAI-compatible embedding provider in Settings; Alibaba
  Cloud DashScope and SiliconFlow work out of the box. Without a key Ling
  keeps using keyword-only search.
- Keep a local SQLite index in `.ling/code-index` with incremental updates:
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
