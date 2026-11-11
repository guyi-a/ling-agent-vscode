import { useEffect, useState, type FormEvent } from "react";
import type {
  DiffView,
  EffortLevel,
  SettingsView,
} from "../types";
import { EyeIcon, EyeOffIcon } from "./icons";

interface SettingsPanelProps {
  settings: SettingsView | null;
  loading: boolean;
  saving: boolean;
  error: string;
  notice: string;
  onSave(input: {
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
  }): void;
  onClose(): void;
}

export function SettingsPanel({
  settings,
  loading,
  saving,
  error,
  notice,
  onSave,
  onClose,
}: SettingsPanelProps) {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [effort, setEffort] = useState<EffortLevel>("high");
  const [diffView, setDiffView] = useState<DiffView>("inline");
  const [maxSteps, setMaxSteps] = useState("10");
  const [maxParallelTools, setMaxParallelTools] = useState("4");
  const [commandTimeout, setCommandTimeout] = useState("120");
  const [approvalTimeout, setApprovalTimeout] = useState("120");
  const [contextWindow, setContextWindow] = useState("1000000");
  const [keepTurns, setKeepTurns] = useState("5");
  const [showKey, setShowKey] = useState(false);

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
    setShowKey(false);
  }, [settings]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (
      !model.trim() ||
      !baseUrl.trim() ||
      !numbersValid() ||
      !settings
    ) {
      return;
    }

    onSave({
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
    });
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
          <form onSubmit={handleSubmit}>
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
                  autoFocus
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
                <span className="settings-label">Max steps</span>
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
                step={1024}
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
                        {skill.source === "workspace"
                          ? skill.overridesBuiltin
                            ? "workspace · overrides builtin"
                            : "workspace"
                          : "builtin"}
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

            <div className="settings-actions">
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
                disabled={!canSave}
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </form>
        )}
      </div>
    </section>
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
