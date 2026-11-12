import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { ApprovalTakeover } from "./components/ApprovalTakeover";
import { BootScreen } from "./components/BootScreen";
import { ChatSelectionButton } from "./components/ChatSelectionButton";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { Header } from "./components/Header";
import { HistoryPanel } from "./components/HistoryPanel";
import { QuestionTakeover } from "./components/QuestionTakeover";
import { SettingsPanel } from "./components/SettingsPanel";
import { Timeline } from "./components/Timeline";
import { createHostBridge } from "./host";
import { useHostEvents } from "./hooks/useHostEvents";
import { useScriptedEvents } from "./hooks/useScriptedEvents";
import {
  createEmptyTimelineState,
  timelineReducer,
} from "./state/timelineReducer";
import type {
  ApprovalMode,
  AskUserAnswer,
  EffortLevel,
  UserAttachmentView,
  UserSelectionRef,
  UserSelectionSummary,
  UserSelectionView,
} from "./types";
import { toAttachmentRefs } from "../../shared/attachments";
import { toSelectionRefs } from "../../shared/selections";
import {
  MAX_ATTACHMENTS_PER_MESSAGE,
  UI_PROTOCOL_VERSION,
  type WebviewToHostMessage,
} from "../../shared/transport";
import type {
  HistorySession,
  IndexBuildStats,
  IndexStatus,
  SettingsView,
} from "../../shared/uiTypes";

export function App() {
  const host = useMemo(createHostBridge, []);
  const announcedReady = useRef(false);
  const copyNoticeTimer = useRef<number | undefined>(undefined);
  const modeNoticeTimer = useRef<number | undefined>(undefined);
  const buildAfterSave = useRef(false);
  const [booting, setBooting] = useState(host.kind !== "browser");
  const [state, dispatch] = useReducer(
    timelineReducer,
    createEmptyTimelineState(),
  );
  const [streamKey, setStreamKey] = useState(0);
  const [mode, setMode] = useState<ApprovalMode>("manual");
  const [effort, setEffort] = useState<EffortLevel>("high");
  const [notice, setNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDeleting, setHistoryDeleting] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historySessions, setHistorySessions] = useState<HistorySession[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsView, setSettingsView] =
    useState<SettingsView | null>(null);
  const [settingsError, setSettingsError] = useState("");
  const [settingsNotice, setSettingsNotice] = useState("");
  const [indexBuilding, setIndexBuilding] = useState(false);
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null);
  const [indexError, setIndexError] = useState("");
  const [attachments, setAttachments] = useState<UserAttachmentView[]>([]);
  const [selections, setSelections] = useState<UserSelectionView[]>([]);
  const attachmentRequestSeq = useRef(0);
  const [workspaceName, setWorkspaceName] = useState(
    host.kind === "browser" ? "ling-vscode" : "Workspace",
  );

  const handleHistoryList = useCallback(
    (sessions: HistorySession[], error?: string) => {
      if (!error) {
        setHistorySessions(sessions);
      }
      setHistoryLoading(false);
      setHistoryDeleting(false);
      setHistoryError(error ?? "");
    },
    [],
  );
  const handleWorkspaceName = useCallback((name: string) => {
    setWorkspaceName(name);
  }, []);
  const handleApprovalMode = useCallback((next: ApprovalMode) => {
    setMode(next);
  }, []);
  const handleSettings = useCallback(
    (next: SettingsView, error?: string) => {
      setSettingsView(next);
      setEffort(next.effort);
      setSettingsLoading(false);
      setSettingsError(error ?? "");
    },
    [],
  );
  const handleSettingsSaved = useCallback(
    (
      next?: SettingsView,
      error?: string,
      warning?: string,
    ) => {
      setSettingsSaving(false);
      if (next) {
        setSettingsView(next);
        setEffort(next.effort);
      }
      if (error) {
        if (buildAfterSave.current) {
          buildAfterSave.current = false;
          setIndexBuilding(false);
        }
        setSettingsError(error);
        setSettingsNotice("");
        return;
      }
      setSettingsError("");
      setSettingsNotice(
        warning ?? "Settings saved. The sidecar was restarted.",
      );
      if (buildAfterSave.current) {
        buildAfterSave.current = false;
        setIndexBuilding(true);
        setIndexError("");
        host.postMessage({ type: "index.build" });
      }
    },
    [host],
  );
  const handleAttachmentSaved = useCallback(
    (
      _requestId: string,
      attachment?: UserAttachmentView,
      error?: string,
    ) => {
      if (error) {
        setNotice(error);
        return;
      }
      if (!attachment) {
        return;
      }
      setAttachments((current) => {
        if (
          current.length >= MAX_ATTACHMENTS_PER_MESSAGE ||
          current.some(
            (item) => item.relativePath === attachment.relativePath,
          )
        ) {
          return current;
        }
        return [...current, attachment];
      });
      setNotice("");
    },
    [],
  );
  const attachFiles = useCallback(
    async (files: File[]) => {
      if (host.kind !== "vscode") {
        return;
      }
      const images = files.filter((file) =>
        file.type.startsWith("image/"),
      );
      if (images.length === 0) {
        return;
      }

      const remaining =
        MAX_ATTACHMENTS_PER_MESSAGE - attachments.length;
      if (remaining <= 0) {
        setNotice(
          `You can attach up to ${MAX_ATTACHMENTS_PER_MESSAGE} images per message.`,
        );
        return;
      }
      if (images.length > remaining) {
        setNotice(
          `Only ${remaining} more image${remaining === 1 ? "" : "s"} can be attached.`,
        );
      }

      for (const file of images.slice(0, remaining)) {
        try {
          const dataBase64 = await readFileAsBase64(file);
          const requestId = `attachment-${Date.now().toString(36)}-${++attachmentRequestSeq.current}`;
          host.postMessage({
            type: "attachment.save",
            requestId,
            name: file.name || "pasted.png",
            mimeType: file.type,
            dataBase64,
          });
        } catch (error) {
          setNotice(
            `Unable to attach image: ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
    },
    [attachments.length, host],
  );
  const removeAttachment = useCallback((relativePath: string) => {
    setAttachments((current) =>
      current.filter((item) => item.relativePath !== relativePath),
    );
  }, []);

  const addSelection = useCallback(
    (selection: UserSelectionRef | UserSelectionView) => {
      setSelections((current) => {
        if (current.length >= 4) {
          setNotice("Up to 4 selections can be added to one message.");
          return current;
        }
        const duplicate = current.some(
          (item) =>
            item.kind === selection.kind &&
            item.label === selection.label &&
            item.path === selection.path &&
            item.startLine === selection.startLine &&
            item.endLine === selection.endLine &&
            item.text === selection.text,
        );
        if (duplicate) {
          return current;
        }
        return [
          ...current,
          {
            ...selection,
            id:
              "id" in selection && selection.id
                ? selection.id
                : `selection-${Date.now()}-${Math.random()
                    .toString(36)
                    .slice(2, 8)}`,
          },
        ];
      });
      setNotice("");
    },
    [],
  );

  const removeSelection = useCallback((id: string) => {
    setSelections((current) =>
      current.filter((selection) => selection.id !== id),
    );
  }, []);

  const handleSelectionAdded = useCallback(
    (selection?: UserSelectionView, error?: string) => {
      if (selection) {
        addSelection(selection);
      }
      if (error) {
        setNotice(error);
      }
    },
    [addSelection],
  );

  const handleIndexResult = useCallback(
    (ok: boolean, stats?: IndexBuildStats, error?: string) => {
      setIndexBuilding(false);
      if (!ok || !stats) {
        setIndexError(error ?? "Index build failed.");
        return;
      }
      setIndexStatus(stats);
      setIndexError(stats.error ?? "");
    },
    [],
  );

  const handleIndexStatus = useCallback((status: IndexStatus) => {
    setIndexStatus(status);
  }, []);

  const handleReady = useCallback(() => {
    setBooting(false);
  }, []);

  function buildIndex(
    input: Omit<
      Extract<WebviewToHostMessage, { type: "settings.save" }>,
      "type"
    >,
  ) {
    if (host.kind !== "vscode") {
      setIndexError("Index builds are only available in VS Code.");
      return;
    }
    setIndexBuilding(true);
    setIndexError("");
    // Build with the values currently in the form; save first so the
    // sidecar restarts with the new embedding configuration.
    saveSettings(input, true);
  }

  useHostEvents(
    host,
    dispatch,
    handleHistoryList,
    handleWorkspaceName,
    handleApprovalMode,
    handleSettings,
    handleSettingsSaved,
    handleAttachmentSaved,
    handleSelectionAdded,
    handleIndexResult,
    handleIndexStatus,
    handleReady,
  );
  useScriptedEvents(dispatch, streamKey, host.kind === "browser");

  useEffect(() => {
    if (!booting) {
      return;
    }
    const timer = window.setTimeout(() => setBooting(false), 5_000);
    return () => window.clearTimeout(timer);
  }, [booting]);

  useEffect(
    () => () => window.clearTimeout(copyNoticeTimer.current),
    [],
  );

  useEffect(() => {
    if (!historyOpen) {
      return;
    }
    const timer = window.setTimeout(
      () => {
        host.postMessage({
          type: "history.search",
          query: historyQuery,
        });
      },
      historyQuery.trim() ? 250 : 0,
    );
    return () => window.clearTimeout(timer);
  }, [historyOpen, historyQuery, host]);

  useEffect(() => {
    document.body.dataset.host = host.kind;
  }, [host.kind]);

  useEffect(() => {
    if (announcedReady.current) {
      return;
    }

    announcedReady.current = true;
    host.postMessage({
      type: "ui.ready",
      host: host.kind,
      protocol: UI_PROTOCOL_VERSION,
    });
    if (host.kind === "vscode") {
      host.postMessage({ type: "settings.get" });
    }
  }, [host]);

  const request = state.pendingApprovals[0];
  const question = state.pendingQuestions?.[0];
  const queuedMessages = state.queuedMessages ?? [];
  const readOnly = state.readOnly === true;
  const busy =
    state.phase === "running" || state.phase === "waiting_approval";
  const approvalIndex = 0;
  const approvalTotal = state.pendingApprovals.length;
  const questionTotal = state.pendingQuestions?.length ?? 0;
  const isEmpty =
    state.entries.length === 0 &&
    state.phase === "idle" &&
    !readOnly &&
    !request &&
    !question;
  const firstUserText = state.entries.find(
    (entry) => entry.kind === "message" && entry.message.role === "user",
  );
  const historyTitle = historySessions.find(
    (session) => session.id === state.sessionId,
  )?.title;
  const headerTitle =
    (isEmpty
      ? workspaceName
      : historyTitle ??
        (state.readOnly
          ? "Past session"
          : firstUserText?.kind === "message" &&
              firstUserText.message.content
            ? truncate(firstUserText.message.content, 34)
            : workspaceName));

  function resolve(approved: boolean) {
    if (!request) {
      return;
    }

    if (host.kind === "browser") {
      dispatch({
        type: "approval.resolved",
        approvalId: request.id,
        approved,
      });

      const toolEntry = state.entries.find(
        (entry) =>
          entry.kind === "tool" &&
          entry.tool.action === request.action &&
          entry.tool.target === request.target,
      );
      if (toolEntry?.kind === "tool") {
        dispatch({
          type: "tool.updated",
          tool: {
            ...toolEntry.tool,
            status: approved ? "Completed" : "Failed",
          },
        });
      }
    }

    host.postMessage({
      type: "approval.resolve",
      approvalId: request.id,
      effectDigest: request.effectDigest,
      approved,
    });
    setNotice("");
  }

  function answerQuestion(answers: AskUserAnswer[]) {
    if (!question) {
      return;
    }

    if (host.kind === "browser") {
      dispatch({
        type: "question.resolved",
        callId: question.callId,
        answers,
      });
    }

    host.postMessage({
      type: "question.answer",
      callId: question.callId,
      answers,
    });
    setNotice("");
  }

  function sendQueuedNow(queuedId: string) {
    host.postMessage({
      type: "session.queued.sendNow",
      queuedId,
    });
    setNotice("");
  }

  function cancelRun() {
    if (host.kind === "browser") {
      dispatch({ type: "run.cancelled" });
    }
    host.postMessage({ type: "run.cancel" });
    setNotice("");
  }

  function resetSession() {
    dispatch({ type: "session.reset" });
    if (host.kind === "browser") {
      setStreamKey((current) => current + 1);
    } else {
      host.postMessage({ type: "session.new" });
    }
    setHistoryOpen(false);
    setSettingsOpen(false);
    setHistoryQuery("");
    setHistoryError("");
    setHistoryDeleting(false);
    setAttachments([]);
    setSelections([]);
    setNotice("");
  }

  function openHistory() {
    if (host.kind !== "vscode") {
      return;
    }
    setSettingsOpen(false);
    setHistoryOpen(true);
    setHistoryLoading(true);
    setHistoryDeleting(false);
    setHistoryQuery("");
    setHistoryError("");
  }

  function openHistoricalSession(sessionId: string) {
    if (busy) {
      return;
    }
    host.postMessage({ type: "history.open", sessionId });
    setHistoryOpen(false);
    setSettingsOpen(false);
    setHistoryError("");
    setAttachments([]);
    setSelections([]);
    setNotice("");
  }

  function deleteHistorySession(sessionId: string) {
    if (busy || historyDeleting) {
      return;
    }
    setHistoryDeleting(true);
    setHistoryError("");
    host.postMessage({ type: "history.delete", sessionId });
  }

  function openSettings() {
    if (busy) {
      return;
    }
    setHistoryOpen(false);
    setSettingsOpen(true);
    setSettingsLoading(true);
    setSettingsError("");
    setSettingsNotice("");
    host.postMessage({ type: "settings.get" });
  }

  function closeSettings() {
    setSettingsOpen(false);
    setSettingsError("");
    setSettingsNotice("");
  }

  function saveSettings(
    input: Omit<
      Extract<WebviewToHostMessage, { type: "settings.save" }>,
      "type"
    >,
    andBuild = false,
  ) {
    buildAfterSave.current = andBuild;
    setSettingsSaving(true);
    setSettingsError("");
    setSettingsNotice("");
    host.postMessage({ type: "settings.save", ...input });
  }

  function deleteMessage(messageId: string) {
    host.postMessage({ type: "message.delete", messageId });
    setNotice("");
  }

  function regenerateMessage(messageId: string) {
    host.postMessage({
      type: "message.regenerate",
      messageId,
      effort,
    });
    setNotice("");
  }

  function send(
    text: string,
    attachmentsToSend: UserAttachmentView[] = attachments,
  ) {
    host.postMessage({
      type: "chat.submit",
      text,
      approvalMode: mode,
      effort,
      ...(attachmentsToSend.length > 0
        ? { attachments: toAttachmentRefs(attachmentsToSend) }
        : {}),
      ...(selections.length > 0
        ? { selections: toSelectionRefs(selections) }
        : {}),
    });
    setAttachments([]);
    setSelections([]);
    setNotice(
      host.kind === "browser"
        ? "Preview mode uses a scripted event stream."
        : "",
    );
  }

  function changeMode(next: ApprovalMode) {
    setMode(next);
    host.postMessage({ type: "mode.change", mode: next });
  }

  useEffect(() => {
    function handleModeShortcut(event: KeyboardEvent) {
      if (event.key !== "Tab" || !event.shiftKey) {
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      if (settingsOpen || historyOpen) {
        return;
      }
      event.preventDefault();
      const order: ApprovalMode[] = [
        "manual",
        "accept-write",
        "auto",
        "plan",
      ];
      const labels: Record<ApprovalMode, string> = {
        manual: "Manual",
        "accept-write": "Edit automatically",
        auto: "Auto",
        plan: "Plan",
      };
      const index = order.indexOf(mode);
      const next = order[(index + 1) % order.length];
      changeMode(next);
      setNotice(`Mode: ${labels[next]}`);
      window.clearTimeout(modeNoticeTimer.current);
      modeNoticeTimer.current = window.setTimeout(
        () => setNotice(""),
        1_600,
      );
    }

    window.addEventListener("keydown", handleModeShortcut, true);
    return () =>
      window.removeEventListener("keydown", handleModeShortcut, true);
  }, [mode, settingsOpen, historyOpen]);

  function changeEffort(next: EffortLevel) {
    setEffort(next);
    if (host.kind === "vscode") {
      host.postMessage({ type: "effort.change", effort: next });
    }
  }

  function openAttachment(relativePath: string) {
    host.postMessage({ type: "attachment.open", relativePath });
  }

  function openPlan(path: string) {
    if (host.kind !== "vscode") {
      setNotice("Plans are only available in VS Code.");
      return;
    }
    host.postMessage({ type: "plan.open", path });
  }

  function buildPlan(path: string) {
    if (host.kind !== "vscode") {
      setNotice("Plans are only available in VS Code.");
      return;
    }
    host.postMessage({ type: "plan.build", path });
  }

  function copyToClipboard(text: string, noticeText: string) {
    if (host.kind === "vscode") {
      host.postMessage({ type: "selection.copy", text });
    } else {
      void navigator.clipboard.writeText(text);
    }
    setNotice(noticeText);
    window.clearTimeout(copyNoticeTimer.current);
    copyNoticeTimer.current = window.setTimeout(() => {
      setNotice((current) =>
        current === noticeText ? "" : current,
      );
    }, 1600);
  }

  function copySelection(text: string) {
    copyToClipboard(text, "Selection copied.");
  }

  function copyMessage(text: string) {
    copyToClipboard(text, "Copied.");
  }

  function openSelection(selection: UserSelectionSummary) {
    if (!selection.path) {
      return;
    }
    host.postMessage({
      type: "selection.open",
      path: selection.path,
      ...(selection.startLine !== undefined
        ? { startLine: selection.startLine }
        : {}),
      ...(selection.endLine !== undefined
        ? { endLine: selection.endLine }
        : {}),
    });
  }

  if (booting) {
    return (
      <div className={`app-shell app-shell--${host.kind}`}>
        <BootScreen />
      </div>
    );
  }

  return (
    <div className={`app-shell app-shell--${host.kind}`}>
      <Header
        title={headerTitle}
        isNew={isEmpty}
        historyAvailable={host.kind === "vscode"}
        settingsDisabled={busy}
        onHistory={openHistory}
        onSettings={openSettings}
        onReset={resetSession}
      />

      {historyOpen ? (
        <HistoryPanel
          sessions={historySessions}
          loading={historyLoading}
          deleting={historyDeleting}
          switchingDisabled={busy}
          query={historyQuery}
          error={historyError}
          onQueryChange={(query) => {
            setHistoryQuery(query);
            setHistoryError("");
          }}
          onSelect={openHistoricalSession}
          onDelete={deleteHistorySession}
          onClose={() => setHistoryOpen(false)}
          onNewSession={resetSession}
        />
      ) : settingsOpen ? (
        <SettingsPanel
          settings={settingsView}
          loading={settingsLoading}
          saving={settingsSaving}
          error={settingsError}
          notice={settingsNotice}
          indexBuilding={indexBuilding}
          indexStatus={indexStatus}
          indexError={indexError}
          onBuildIndex={buildIndex}
          onSave={saveSettings}
          onClose={closeSettings}
        />
      ) : null}

      {isEmpty ? (
        <EmptyState />
      ) : (
        <Timeline
          state={state}
          onOpenAttachment={openAttachment}
          onOpenSelection={openSelection}
          onOpenPlan={openPlan}
          onBuildPlan={buildPlan}
          onCopyMessage={copyMessage}
          onDeleteMessage={deleteMessage}
          onRegenerateMessage={regenerateMessage}
          actionsDisabled={busy || readOnly}
        />
      )}

      <ChatSelectionButton
        onQuote={addSelection}
        onCopy={copySelection}
      />

      <footer className="bottom-slot">
        {!readOnly && queuedMessages.length > 0 ? (
          <div className="composer-queue" aria-label="Queued messages">
            <p className="composer-queue-title">
              {queuedMessages.length} queued message
              {queuedMessages.length === 1 ? "" : "s"}
            </p>
            {queuedMessages.map((message) => (
              <div className="composer-queue-item" key={message.id}>
                <span className="composer-queue-text" title={message.text}>
                  {truncate(message.text, 90)}
                </span>
                {message.attachmentCount > 0 ? (
                  <small>{message.attachmentCount} file(s)</small>
                ) : null}
                <button
                  className="composer-queue-send"
                  type="button"
                  title="Interrupt the current run and send this message now"
                  onClick={() => sendQueuedNow(message.id)}
                >
                  Send now
                </button>
              </div>
            ))}
          </div>
        ) : null}

        {notice ? (
          <p className="composer-notice" role="status">
            {notice}
          </p>
        ) : null}

        {readOnly ? (
          <div className="past-session-footer">
            <div>
              <strong>Past session</strong>
              <p>This conversation is read-only.</p>
            </div>
            <button
              className="button button--primary"
              type="button"
              onClick={resetSession}
            >
              New session
            </button>
          </div>
        ) : request ? (
          <ApprovalTakeover
            request={request}
            index={approvalIndex}
            total={approvalTotal}
            onResolve={resolve}
            onCancelRun={cancelRun}
          />
        ) : question ? (
          <QuestionTakeover
            request={question}
            index={0}
            total={questionTotal}
            onAnswer={answerQuestion}
            onCancelRun={cancelRun}
          />
        ) : (
          <Composer
            mode={mode}
            effort={effort}
            attachments={attachments}
            selections={selections}
            attachEnabled={host.kind === "vscode"}
            busy={busy}
            onModeChange={changeMode}
            onEffortChange={changeEffort}
            onAttachFiles={(files) => {
              void attachFiles(files);
            }}
            onPickAttachments={() => {
              host.postMessage({ type: "attachment.pick" });
            }}
            onRemoveAttachment={removeAttachment}
            onRemoveSelection={removeSelection}
            onSend={send}
            onStop={cancelRun}
          />
        )}
      </footer>
    </div>
  );
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => {
      reject(reader.error ?? new Error("Unable to read image"));
    };
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("Unexpected image data"));
        return;
      }
      const comma = reader.result.indexOf(",");
      resolve(
        comma >= 0 ? reader.result.slice(comma + 1) : reader.result,
      );
    };
    reader.readAsDataURL(file);
  });
}

function truncate(value: string, limit: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit - 1)}…`;
}
