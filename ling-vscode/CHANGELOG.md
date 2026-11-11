# Changelog

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
