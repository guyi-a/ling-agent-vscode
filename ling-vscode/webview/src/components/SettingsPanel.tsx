import { useEffect, useState, type FormEvent } from "react";
import type {
  DiffView,
  EffortLevel,
  IndexStatus,
  MemoryScope,
  McpServerStatus,
  SettingsView,
} from "../types";
import {
  EyeIcon,
  EyeOffIcon,
  RefreshIcon,
} from "./icons";

interface SettingsInput {
  apiKey: string;
  model: string;
  baseUrl: string;
  effort: EffortLevel;
  diffView: DiffView;
  maxSteps: number;
  maxParallelTools: number;
  commandTimeoutSeconds: number;
  approvalTimeoutSeconds: number;
  contextWindowTokens: number;
  keepLastUserTurns: number;
  embeddingModel: string;
  embeddingBaseUrl: string;
  embeddingApiKey: string;
}

interface SettingsPanelProps {
  settings: SettingsView | null;
  loading: boolean;
  saving: boolean;
  error: string;
  notice: string;
  indexBuilding: boolean;
  indexStatus: IndexStatus | null;
  indexError: string;
  onBuildIndex(input: SettingsInput): void;
  onSave(input: SettingsInput): void;
  onOpenMemory(path: string): void;
  onCreateMemory(scope: MemoryScope): void;
  onDeleteMemory(scope: MemoryScope, name: string): void;
  onOpenMcpConfig(): void;
  onAuthorizeMcp(name: string): void;
  onRevokeMcpAuth(name: string): void;
  onClose(): void;
}

export function SettingsPanel({
  settings,
  loading,
  saving,
  error,
  notice,
  indexBuilding,
  indexStatus,
  indexError,
  onBuildIndex,
  onSave,
  onOpenMemory,
  onCreateMemory,
  onDeleteMemory,
  onOpenMcpConfig,
  onAuthorizeMcp,
  onRevokeMcpAuth,
  onClose,
}: SettingsPanelProps) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [effort, setEffort] = useState<EffortLevel>("high");
  const [diffView, setDiffView] = useState<DiffView>("inline");
  const [maxSteps, setMaxSteps] = useState("50");
  const [maxParallelTools, setMaxParallelTools] = useState("4");
  const [commandTimeout, setCommandTimeout] = useState("120");
  const [approvalTimeout, setApprovalTimeout] = useState("120");
  const [contextWindow, setContextWindow] = useState("1000000");
  const [keepTurns, setKeepTurns] = useState("5");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState("");
  const [embeddingApiKey, setEmbeddingApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [showEmbeddingKey, setShowEmbeddingKey] = useState(false);

  useEffect(() => {
    if (!settings) {
      return;
    }
    setApiKey(settings.apiKey);
    setModel(settings.model);
    setBaseUrl(settings.baseUrl);
    setEffort(settings.effort);
    setDiffView(settings.diffView);
    setMaxSteps(String(settings.maxSteps));
    setMaxParallelTools(String(settings.maxParallelTools));
    setCommandTimeout(String(settings.commandTimeoutSeconds));
    setApprovalTimeout(String(settings.approvalTimeoutSeconds));
    setContextWindow(String(settings.contextWindowTokens));
    setKeepTurns(String(settings.keepLastUserTurns));
    setEmbeddingModel(settings.embeddingModel);
    setEmbeddingBaseUrl(settings.embeddingBaseUrl);
    setEmbeddingApiKey(settings.embeddingApiKey);
    setShowKey(false);
    setShowEmbeddingKey(false);
  }, [settings]);

  function currentInput(): SettingsInput {
    return {
      apiKey: apiKey.trim(),
      model: model.trim(),
      baseUrl: baseUrl.trim(),
      effort,
      diffView,
      maxSteps: Number(maxSteps),
      maxParallelTools: Number(maxParallelTools),
      commandTimeoutSeconds: Number(commandTimeout),
      approvalTimeoutSeconds: Number(approvalTimeout),
      contextWindowTokens: Number(contextWindow),
      keepLastUserTurns: Number(keepTurns),
      embeddingModel: embeddingModel.trim(),
      embeddingBaseUrl: embeddingBaseUrl.trim(),
      embeddingApiKey: embeddingApiKey.trim(),
    };
  }

  function formValid(): boolean {
    return (
      Boolean(model.trim()) &&
      Boolean(baseUrl.trim()) &&
      numbersValid() &&
      settings !== null
    );
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!formValid()) {
      return;
    }
    onSave(currentInput());
  }

  function handleBuild() {
    if (!formValid()) {
      return;
    }
    onBuildIndex(currentInput());
  }

  function numbersValid(): boolean {
    return (
      isIntegerInRange(maxSteps, 1, 100) &&
      isIntegerInRange(maxParallelTools, 1, 16) &&
      isNumberInRange(commandTimeout, 1, 3600) &&
      isNumberInRange(approvalTimeout, 1, 3600) &&
      isIntegerInRange(contextWindow, 1024, 10_000_000) &&
      isIntegerInRange(keepTurns, 0, 50)
    );
  }

  const canSave =
    settings !== null &&
    !saving &&
    Boolean(model.trim()) &&
    Boolean(baseUrl.trim()) &&
    numbersValid();

  return (
    <section className="settings-panel" aria-label="Settings">
      <header className="settings-header">
        <h2>Settings</h2>
        <button
          className="history-close"
          type="button"
          aria-label="Close settings"
          onClick={onClose}
        >
          ×
        </button>
      </header>

      <div className="settings-body">
        {loading && !settings ? (
          <p className="settings-loading">Loading settings...</p>
        ) : !settings ? (
          <p className="settings-loading">Settings are unavailable.</p>
        ) : (
          <form
            id="ling-settings-form"
            onSubmit={handleSubmit}
            noValidate
          >
            <div className="settings-field">
              <label
                className="settings-label"
                htmlFor="ling-api-key"
              >
                API Key
              </label>
              <div className="settings-key-input">
                <input
                  id="ling-api-key"
                  className="settings-input settings-input--key"
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                  placeholder="sk-..."
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setApiKey(event.target.value)}
                />
                <button
                  className="settings-eye"
                  type="button"
                  aria-label={showKey ? "Hide API key" : "Show API key"}
                  title={showKey ? "Hide API key" : "Show API key"}
                  onClick={() => setShowKey((value) => !value)}
                >
                  {showKey ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
              {!apiKey.trim() ? (
                <p className="settings-hint">
                  Leave empty to remove the saved key.
                </p>
              ) : null}
            </div>

            <label className="settings-field">
              <span className="settings-label">Model</span>
              <input
                className="settings-input"
                value={model}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => setModel(event.target.value)}
              />
            </label>

            <label className="settings-field">
              <span className="settings-label">API Base URL</span>
              <input
                className="settings-input"
                value={baseUrl}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </label>

            <p className="settings-section">Semantic search (optional)</p>

            <div className="settings-field">
              <label
                className="settings-label"
                htmlFor="ling-embedding-model"
              >
                Embedding model
              </label>
              <input
                id="ling-embedding-model"
                className="settings-input"
                value={embeddingModel}
                placeholder="text-embedding-v4"
                spellCheck={false}
                autoComplete="off"
                onChange={(event) =>
                  setEmbeddingModel(event.target.value)
                }
              />
              <p className="settings-hint">
                Change this when using another provider.
              </p>
            </div>

            <label className="settings-field">
              <span className="settings-label">
                Embedding base URL
              </span>
              <input
                className="settings-input"
                value={embeddingBaseUrl}
                placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                spellCheck={false}
                autoComplete="off"
                onChange={(event) =>
                  setEmbeddingBaseUrl(event.target.value)
                }
              />
            </label>

            <div className="settings-field">
              <label
                className="settings-label"
                htmlFor="ling-embedding-key"
              >
                Embedding API Key
              </label>
              <div className="settings-key-input">
                <input
                  id="ling-embedding-key"
                  className="settings-input settings-input--key"
                  type={showEmbeddingKey ? "text" : "password"}
                  value={embeddingApiKey}
                  placeholder="Defaults to the API Key above"
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) =>
                    setEmbeddingApiKey(event.target.value)
                  }
                />
                <button
                  className="settings-eye"
                  type="button"
                  aria-label={
                    showEmbeddingKey
                      ? "Hide embedding API key"
                      : "Show embedding API key"
                  }
                  title={
                    showEmbeddingKey
                      ? "Hide embedding API key"
                      : "Show embedding API key"
                  }
                  onClick={() =>
                    setShowEmbeddingKey((value) => !value)
                  }
                >
                  {showEmbeddingKey ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
              <p className="settings-hint">
                Leave empty for keyword-only search.
              </p>
            </div>

            <div className="settings-examples">
              <p className="settings-examples__title">
                Compatible providers
              </p>
              <div className="settings-example">
                <span className="settings-example__name">
                  Alibaba Cloud
                </span>
                <span className="settings-example__value">
                  dashscope.aliyuncs.com/compatible-mode/v1 ·
                  text-embedding-v4
                </span>
              </div>
              <div className="settings-example">
                <span className="settings-example__name">
                  SiliconFlow
                </span>
                <span className="settings-example__value">
                  api.siliconflow.cn/v1 · BAAI/bge-m3
                </span>
              </div>
              <p className="settings-hint">
                DeepSeek has no embeddings API. The chat key is reused
                only when both base URLs match.
              </p>
            </div>

            <p className="settings-section">Code index</p>

            <div className="settings-index">
              <div className="settings-index__row">
                <span className="settings-index__percent">
                  {indexStatus ? `${indexStatus.percent}%` : "—"}
                </span>
                <span className="settings-index__count">
                  {indexStatus
                    ? `${indexStatus.indexedFiles} / ${
                        indexStatus.model
                          ? indexStatus.files
                          : indexStatus.totalFiles
                      } files indexed`
                    : "Checking index..."}
                </span>
                <button
                  className="settings-index__button"
                  type="button"
                  title="Save settings and build or refresh the code index"
                  disabled={indexBuilding || !canSave}
                  onClick={handleBuild}
                >
                  <RefreshIcon
                    className={indexBuilding ? "spinning" : undefined}
                  />
                  <span>
                    {indexBuilding ? "Building..." : "Build index"}
                  </span>
                </button>
              </div>
              <div
                className={`settings-index__bar${
                  indexStatus ? "" : " settings-index__bar--loading"
                }`}
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={indexStatus?.percent ?? 0}
              >
                <div
                  className="settings-index__fill"
                  style={
                    indexStatus
                      ? { width: `${indexStatus.percent}%` }
                      : undefined
                  }
                />
              </div>
              <p className="settings-hint">
                {indexStatus
                  ? `${indexStatus.chunks} chunks · ${indexStatus.vectors} vectors. `
                  : "Builds the keyword and vector index. "}
                Incremental (only changed chunks are re-embedded).
                Build saves the settings above first. Dependencies and
                build output (node_modules, .venv, dist) are skipped.
              </p>
            </div>
            {indexError ? (
              <p className="settings-error" role="alert">
                {indexError}
              </p>
            ) : null}

            <p className="settings-section">Model behavior</p>

            <label className="settings-field">
              <span className="settings-label">Reasoning effort</span>
              <select
                className="settings-input"
                value={effort}
                onChange={(event) =>
                  setEffort(event.target.value as EffortLevel)
                }
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="xhigh">Extra high</option>
                <option value="max">Max</option>
              </select>
            </label>

            <label className="settings-field">
              <span className="settings-label">Diff view</span>
              <select
                className="settings-input"
                value={diffView}
                onChange={(event) =>
                  setDiffView(event.target.value as DiffView)
                }
              >
                <option value="inline">Inline</option>
                <option value="sideBySide">Side by side</option>
              </select>
            </label>

            <p className="settings-section">Agent</p>

            <div className="settings-grid">
              <label className="settings-field">
                <span className="settings-label">
                  Max steps per message
                </span>
                <input
                  className="settings-input"
                  type="number"
                  min={1}
                  max={100}
                  value={maxSteps}
                  onChange={(event) => setMaxSteps(event.target.value)}
                />
              </label>

              <label className="settings-field">
                <span className="settings-label">Parallel tools</span>
                <input
                  className="settings-input"
                  type="number"
                  min={1}
                  max={16}
                  value={maxParallelTools}
                  onChange={(event) =>
                    setMaxParallelTools(event.target.value)
                  }
                />
              </label>

              <label className="settings-field">
                <span className="settings-label">
                  Command timeout (s)
                </span>
                <input
                  className="settings-input"
                  type="number"
                  min={1}
                  max={3600}
                  value={commandTimeout}
                  onChange={(event) =>
                    setCommandTimeout(event.target.value)
                  }
                />
              </label>

              <label className="settings-field">
                <span className="settings-label">
                  Approval timeout (s)
                </span>
                <input
                  className="settings-input"
                  type="number"
                  min={1}
                  max={3600}
                  value={approvalTimeout}
                  onChange={(event) =>
                    setApprovalTimeout(event.target.value)
                  }
                />
              </label>
            </div>

            <p className="settings-section">Context</p>

            <label className="settings-field">
              <span className="settings-label">Context window tokens</span>
              <input
                className="settings-input"
                type="number"
                min={1024}
                max={10000000}
                value={contextWindow}
                onChange={(event) =>
                  setContextWindow(event.target.value)
                }
              />
            </label>

            <label className="settings-field">
              <span className="settings-label">
                Keep recent user turns
              </span>
              <input
                className="settings-input"
                type="number"
                min={0}
                max={50}
                value={keepTurns}
                onChange={(event) => setKeepTurns(event.target.value)}
              />
            </label>

            <p className="settings-section">MCP</p>
            <p className="settings-hint">
              External stdio and HTTP tools. Servers run only when their
              tools are used; configuration changes apply on the next turn.
            </p>
            <div className="settings-mcp-heading">
              <span
                className="settings-mcp-path"
                title={settings.mcpConfigPath}
              >
                {settings.mcpConfigPath}
              </span>
              <button
                className="settings-memory-button"
                type="button"
                onClick={onOpenMcpConfig}
              >
                Edit config
              </button>
            </div>
            {settings.mcpIssues.length > 0 ? (
              <div className="settings-memory-diagnostics" role="alert">
                {settings.mcpIssues.map((issue, index) => (
                  <p key={`${issue.name ?? "config"}:${index}`}>
                    {issue.name ? `${issue.name}: ` : ""}
                    {issue.message}
                  </p>
                ))}
              </div>
            ) : null}
            {settings.mcpServers.length === 0 ? (
              <p className="settings-hint">
                No MCP servers configured.
              </p>
            ) : (
              <ul className="settings-mcp-servers">
                {settings.mcpServers.map((server) => (
                  <McpServerRow
                    key={server.name}
                    server={server}
                    onAuthorize={() =>
                      onAuthorizeMcp(server.name)
                    }
                    onRevoke={() =>
                      onRevokeMcpAuth(server.name)
                    }
                  />
                ))}
              </ul>
            )}

            <p className="settings-section">Skills</p>
            {settings.skills.length === 0 ? (
              <p className="settings-hint">No skills found.</p>
            ) : (
              <ul className="settings-skills">
                {settings.skills.map((skill) => (
                  <li className="settings-skill" key={skill.name}>
                    <div className="settings-skill-heading">
                      <span className="settings-skill-name">
                        {skill.name}
                      </span>
                      <span className="settings-skill-source">
                        {skill.source}
                      </span>
                    </div>
                    <span
                      className="settings-skill-path"
                      title={skill.path}
                    >
                      {skill.path}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <p className="settings-section">Memory</p>
            <p className="settings-hint">
              Durable project and global guidance. Files are reloaded
              into the system prompt on the next user turn.
            </p>
            <div className="settings-memory-actions">
              <button
                className="settings-memory-button"
                type="button"
                onClick={() => onCreateMemory("workspace")}
              >
                New project memory
              </button>
              <button
                className="settings-memory-button"
                type="button"
                onClick={() => onCreateMemory("global")}
              >
                New global memory
              </button>
            </div>
            {settings.memoryDiagnostics.length > 0 ? (
              <div className="settings-memory-diagnostics" role="alert">
                {settings.memoryDiagnostics.map((item) => (
                  <p key={item}>{item}</p>
                ))}
              </div>
            ) : null}
            {settings.memories.length === 0 ? (
              <p className="settings-hint">No memories found.</p>
            ) : (
              <ul className="settings-memories">
                {settings.memories.map((memory) => {
                  const mode =
                    memory.alwaysApply
                      ? "Always"
                      : memory.globs.length > 0
                        ? "Conditional"
                        : "Off";
                  return (
                    <li
                      className="settings-memory"
                      key={`${memory.scope}:${memory.name}`}
                    >
                      <div className="settings-memory-heading">
                        <span className="settings-memory-name">
                          {memory.name}
                        </span>
                        <span className="settings-memory-badge">
                          {memory.scope}
                        </span>
                        <span
                          className={`settings-memory-mode settings-memory-mode--${mode.toLowerCase()}`}
                        >
                          {mode}
                        </span>
                      </div>
                      <p className="settings-memory-description">
                        {memory.description}
                      </p>
                      {memory.globs.length > 0 ? (
                        <span className="settings-memory-globs">
                          {memory.globs.join(", ")}
                        </span>
                      ) : null}
                      <span
                        className="settings-memory-path"
                        title={memory.path}
                      >
                        {memory.relativePath}
                      </span>
                      <div className="settings-memory-item-actions">
                        <button
                          className="settings-memory-action"
                          type="button"
                          onClick={() => onOpenMemory(memory.path)}
                        >
                          Edit
                        </button>
                        <button
                          className="settings-memory-action settings-memory-action--danger"
                          type="button"
                          onClick={() =>
                            onDeleteMemory(memory.scope, memory.name)
                          }
                        >
                          Delete
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

          </form>
        )}
      </div>

      {settings ? (
        <div className="settings-footer">
          <div className="settings-footer-status">
            {error ? (
              <p className="settings-error" role="alert">
                {error}
              </p>
            ) : null}
            {notice ? (
              <p className="settings-notice" role="status">
                {notice}
              </p>
            ) : null}
          </div>
          <div className="settings-footer-actions">
            <button
              className="button button--secondary"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              className="button button--primary"
              type="submit"
              form="ling-settings-form"
              disabled={!canSave}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function McpServerRow({
  server,
  onAuthorize,
  onRevoke,
}: {
  server: McpServerStatus;
  onAuthorize(): void;
  onRevoke(): void;
}) {
  const state =
    server.state === "connected"
      ? "Connected"
      : server.state === "configured"
        ? "Configured"
        : server.state === "disabled"
          ? "Disabled"
          : server.state === "needs-auth"
            ? "Needs authorization"
          : server.state === "error"
            ? "Error"
            : "Not connected";
  return (
    <li className="settings-mcp-server">
      <div className="settings-mcp-server-heading">
        <span className="settings-memory-name">{server.name}</span>
        <span className="settings-memory-badge">{server.kind}</span>
        <span
          className={`settings-mcp-state settings-mcp-state--${server.state}`}
        >
          {state}
        </span>
        <span className="settings-mcp-tools">
          {server.toolCount} tool
          {server.toolCount === 1 ? "" : "s"}
        </span>
      </div>
      <span
        className="settings-skill-path"
        title={server.target}
      >
        {server.target}
      </span>
      {server.error ? (
        <p className="settings-mcp-error">{server.error}</p>
      ) : null}
      {server.auth === "oauth" ? (
        <div className="settings-mcp-actions">
          <button
            className="settings-memory-action"
            type="button"
            onClick={server.authorized ? onRevoke : onAuthorize}
          >
            {server.authorized ? "Revoke auth" : "Authorize"}
          </button>
        </div>
      ) : null}
    </li>
  );
}

function isIntegerInRange(
  value: string,
  min: number,
  max: number,
): boolean {
  const parsed = Number(value);
  return (
    value.trim() !== "" &&
    Number.isInteger(parsed) &&
    parsed >= min &&
    parsed <= max
  );
}

function isNumberInRange(
  value: string,
  min: number,
  max: number,
): boolean {
  const parsed = Number(value);
  return (
    value.trim() !== "" &&
    Number.isFinite(parsed) &&
    parsed >= min &&
    parsed <= max
  );
}
