import { useEffect, useState, type FormEvent } from "react";
import type { SettingsView } from "../types";
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
  const [showKey, setShowKey] = useState(false);

  useEffect(() => {
    if (!settings) {
      return;
    }
    setApiKey(settings.apiKey);
    setModel(settings.model);
    setBaseUrl(settings.baseUrl);
    setShowKey(false);
  }, [settings]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!model.trim() || !baseUrl.trim()) {
      return;
    }

    onSave({
      apiKey: apiKey.trim(),
      model: model.trim(),
      baseUrl: baseUrl.trim(),
    });
  }

  const canSave =
    settings !== null &&
    !saving &&
    Boolean(model.trim()) &&
    Boolean(baseUrl.trim());

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
