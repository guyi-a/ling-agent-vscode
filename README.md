# Ling

**Harness your workflow without leaving VS Code.**

Ling is a local agent harness for VS Code. It handles tools, approvals, skills, and context, so you can stay in your editor and keep moving.

- Marketplace: <https://marketplace.visualstudio.com/items?itemName=ling-agent.ling-agent>
- User guide: [`ling-vscode/README.md`](ling-vscode/README.md)
- Engineering guide: [`AGENTS.md`](AGENTS.md)

## Architecture

```text
VS Code extension (ling-vscode/)
  extension.ts / SidecarClient / React webview
                 │
                 │ stdio JSON-RPC (NDJSON)
                 ▼
Local Python harness (ling/)
  ling_rpc -> ling_agent -> ling_core -> ling_ai
                 │
                 ├─ tools (files / search / commands / documents /
                 │         browser / web / MCP / skills / memory / plan)
                 ├─ approvals / file state / local settings
                 ├─ context compaction
                 ├─ retrieval (BM25 + embeddings + RRF)
                 └─ storage (SQLite)
```

The extension provides the chat UI, editor integration, approval diffs, settings, and history. The Python harness owns the agent loop, model streaming, tools, retrieval, and persistence. The harness ships as a single executable inside the VSIX; in development it runs directly from `ling/`.

## Repository layout

| Path | Contents |
|---|---|
| `ling-vscode/` | VS Code extension, React webview, build scripts |
| `ling/` | Local Python agent harness |
| `.agents/skills/` | Source of built-in skills |
| `.github/workflows/` | Test and release CI |
| `AGENTS.md` | Engineering conventions and commands |

Harness layers:

- `ling_ai/`: model-facing types, events, streams, and provider adapter.
- `ling_core/`: generic agent loop, tool registry, hooks.
- `ling_agent/`: sessions, approvals, tools, compaction, retrieval, storage.
- `ling_rpc/`: stdio JSON-RPC server.
- `ling_retrieval/`: BM25, vector search, and evaluation.
- `ling_comment/`: Python docstring and TS/JS JSDoc generation.

## Local development

### Python harness

```powershell
cd ling
python -m pytest -q
```

A single test module can also run directly:

```powershell
python -W error -m tests.test_ling_agent_approvals
```

### VS Code extension

```powershell
cd ling-vscode
npm install
npm test
```

`npm test` runs TypeScript typechecking, webview tests, the extension build, and Python RPC tests.

Useful scripts:

```powershell
npm run build          # typecheck + esbuild + vite
npm run build:sidecar  # PyInstaller onefile
npm run package:vsix   # win32-x64 only
npm run sync:skills    # .agents/skills -> resources/skills
```

### Run the development host

Open `ling-vscode/` in VS Code and press **F5**, or run:

```powershell
code --extensionDevelopmentPath=<repo>/ling-vscode --new-window <repo>
```

Development mode uses the Python source in `ling/`; no sidecar rebuild is needed.

## How it works

- **Approvals**: Manual / Edit automatically / Auto / Plan.
- **Read before edit**: `edit_file` and overwriting `write_file` require a prior `read_file` in the session.
- **Skills**: built-in > global without `source: ling` > workspace; built-ins are mirrored to the user's global skill library.
- **MCP**: user-level configuration with stdio, Streamable HTTP, SSE, and OAuth.
- **Local approval settings**: `.agents/settings.local.json` stores command allow rules and is not committed.
- **Runtime state**: project `.agents/` holds memory, plans, and caches; sessions and credentials live in the user's local app-data directory.

## Release

Versions follow semantic versioning. Before releasing, update `package.json`, `package-lock.json`, and `CHANGELOG.md`, run the full test suite, then:

```powershell
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin main
git push origin vX.Y.Z
```

A `v*` tag triggers GitHub Actions: build the sidecar, run tests, package the VSIX, and publish to the Marketplace via OIDC. No manual VSIX upload or PAT is required.

## More

- User-facing features, settings, privacy, and limits: [`ling-vscode/README.md`](ling-vscode/README.md)
- Engineering conventions and pitfalls: [`AGENTS.md`](AGENTS.md)
- Ling's self-description: `.agents/skills/ling-self/SKILL.md`

## License

MIT. See [`ling-vscode/LICENSE`](ling-vscode/LICENSE).
