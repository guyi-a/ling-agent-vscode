import {
  existsSync,
  readFileSync,
  readdirSync,
  type Dirent,
} from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import * as vscode from "vscode";
import { resolveScratchWorkspaceRoot } from "./scratchWorkspace";
import {
  isIndexStatus,
  isUiEvent,
  isWebviewToHostMessage,
  MAX_ATTACHMENTS_PER_MESSAGE,
  UI_PROTOCOL_VERSION,
  type HostToWebviewMessage,
  type WebviewToHostMessage,
} from "../shared/transport";
import type { UiEvent } from "../shared/uiProtocol";
import type {
  ApprovalMode,
  BackgroundTaskView,
  ControlSessionView,
  EffortLevel,
  HistorySession,
  IndexBuildStats,
  IndexStatus,
  MemoryScope,
  MemoryStatus,
  McpIssue,
  McpServerStatus,
  SessionSnapshot,
  SettingsView,
  SkillStatus,
  UserAttachmentRef,
  UserSelectionRef,
} from "../shared/uiTypes";
import {
  isSupportedImageMime,
  MAX_ATTACHMENT_FILE_BYTES,
  mimeForFileName,
  resolveAttachmentFile,
  saveAttachmentImage,
} from "./attachments/store";
import { CommentCodeLensProvider } from "./comment/commentCodeLens";
import { CommentController } from "./comment/commentController";
import { CommentPreviewController } from "./comment/commentPreview";
import {
  DiffController,
  type ChangeReviewItem,
} from "./diffController";
import { PlanCodeLensProvider } from "./plan/planCodeLens";
import {
  captureEditorSelection,
  captureTerminalSelection,
  toSelectionView,
} from "./selection";
import { SelectionCodeLensProvider } from "./selectionCodeLens";
import {
  DEFAULT_SETTINGS,
  migrateApiKey,
  readSettings,
  settingsPath,
  writeSettings,
  type LingSettings,
} from "./settings/config";
import {
  SidecarClient,
  resolveSkillsRoot,
  type SidecarExitInfo,
  type SidecarSettings,
} from "./sidecar/SidecarClient";

const CHAT_VIEW_ID = "ling.chat";
const WEBVIEW_ROOT = ["media", "webview"] as const;
let activeSidecar: SidecarClient | undefined;

function resolveGlobalSkillsRoot(): string {
  const override = process.env.LING_GLOBAL_SKILLS_ROOT?.trim();
  if (override) {
    return path.resolve(override);
  }
  return path.join(homedir(), ".agents", "skills");
}

function defaultMcpConfigPath(): string {
  const override = process.env.LING_MCP_CONFIG?.trim();
  if (override) {
    return path.resolve(override);
  }
  const appData = process.env.APPDATA?.trim();
  if (appData) {
    return path.join(appData, "Ling", "mcp.json");
  }
  const xdgConfig = process.env.XDG_CONFIG_HOME?.trim();
  if (xdgConfig) {
    return path.join(xdgConfig, "ling-agent", "mcp.json");
  }
  return path.join(homedir(), ".config", "ling-agent", "mcp.json");
}

function browserBridgePortFromEnv(): number | undefined {
  const raw = process.env.LING_BROWSER_BRIDGE_PORT?.trim();
  if (!raw) {
    return undefined;
  }
  const port = Number(raw);
  return Number.isInteger(port) && port >= 0 && port <= 65_535
    ? port
    : undefined;
}

function sidecarSettings(settings: LingSettings): SidecarSettings {
  return {
    apiKey: settings.apiKey || undefined,
    model: settings.model,
    baseUrl: settings.baseUrl,
    maxSteps: settings.maxSteps,
    maxParallelTools: settings.maxParallelTools,
    commandTimeoutSeconds: settings.commandTimeoutSeconds,
    approvalTimeoutSeconds: settings.approvalTimeoutSeconds,
    contextWindowTokens: settings.contextWindowTokens,
    keepLastUserTurns: settings.keepLastUserTurns,
    browserBridgePort: browserBridgePortFromEnv(),
    embeddingModel: settings.embeddingModel || undefined,
    embeddingBaseUrl: settings.embeddingBaseUrl || undefined,
    embeddingApiKey: settings.embeddingApiKey || undefined,
    computerUseEnabled: settings.computerUseEnabled,
    computerSessionTtlSeconds: settings.computerSessionTtlSeconds,
    computerSessionIdleSeconds: settings.computerSessionIdleSeconds,
    computerDeniedApps: settings.computerDeniedApps || undefined,
    computerAuditEnabled: settings.computerAuditEnabled,
  };
}

export async function activate(
  context: vscode.ExtensionContext,
): Promise<void> {
  const output = vscode.window.createOutputChannel("Ling");
  const configPath = settingsPath();
  let settings: LingSettings;
  try {
    settings = await readSettings(configPath);
  } catch (error) {
    output.appendLine(
      `Unable to read Ling settings: ${formatError(error)}`,
    );
    settings = { ...DEFAULT_SETTINGS };
  }

  if (!settings.apiKey) {
    const migration = migrateApiKey(settings);
    if (migration.migrated) {
      settings = migration.settings;
      try {
        await writeSettings(settings, configPath);
        output.appendLine(
          `Migrated API key from ${migration.source} into ${configPath}`,
        );
      } catch (error) {
        output.appendLine(
          `Unable to write Ling settings: ${formatError(error)}`,
        );
      }
    }
  }

  output.appendLine(
    settings.apiKey
      ? `API key loaded from ${configPath}`
      : `No API key configured in ${configPath}`,
  );

  const sidecar = new SidecarClient({
    extensionPath: context.extensionUri.fsPath,
    developmentMode:
      context.extensionMode === vscode.ExtensionMode.Development,
    output: (line) => output.appendLine(line),
    ...sidecarSettings(settings),
  });
  activeSidecar = sidecar;
  const diffController = new DiffController(
    async (approvalId, effectDigest, approved) => {
      await sidecar.start();
      await sidecar.call("approval.resolve", {
        approvalId,
        effectDigest,
        approved,
      });
    },
    (line) => output.appendLine(line),
  );
  const commentPreview = new CommentPreviewController((line) =>
    output.appendLine(line),
  );
  const commentController = new CommentController(
    sidecar,
    commentPreview,
    output,
  );
  const commentCodeLens = new CommentCodeLensProvider();
  const provider = new ChatViewProvider(
    context.extensionUri,
    output,
    sidecar,
    configPath,
    settings,
    diffController,
  );
  sidecar.onExit = (info) => provider.handleSidecarExit(info);
  sidecar.onNotification = (method, params) =>
    provider.handleSidecarNotification(method, params);
  const selectionCodeLens = new SelectionCodeLensProvider();
  const planCodeLens = new PlanCodeLensProvider();

  context.subscriptions.push(
    output,
    sidecar,
    diffController,
    vscode.window.registerWebviewViewProvider(CHAT_VIEW_ID, provider, {
      webviewOptions: {
        retainContextWhenHidden: true,
      },
    }),
    vscode.commands.registerCommand("ling.openChat", async () => {
      await vscode.commands.executeCommand(`${CHAT_VIEW_ID}.focus`);
    }),
    vscode.commands.registerCommand("ling.checkSidecar", async () => {
      try {
        const initialized = await sidecar.restart();
        await sidecar.ping("manual-check");
        await vscode.window.showInformationMessage(
          `Ling sidecar ready: ${initialized.serverInfo.name} ${initialized.serverInfo.version}`,
        );
      } catch (error) {
        output.appendLine(`Sidecar check failed: ${formatError(error)}`);
        await vscode.window.showErrorMessage(
          `Ling sidecar check failed: ${formatError(error)}`,
        );
      }
    }),
    vscode.commands.registerCommand("ling.stopControl", async () =>
      provider.handleControlStop(),
    ),
    vscode.commands.registerCommand("ling.buildIndex", async () =>
      provider.buildIndexFromCommand(),
    ),
    vscode.commands.registerCommand(
      "ling.buildPlan",
      async (uri?: vscode.Uri) => {
        if (uri && uri.scheme === "file") {
          await provider.buildPlanFromUri(uri);
        }
      },
    ),
    vscode.commands.registerCommand("ling.addSelection", async () =>
      provider.addSelection("any"),
    ),
    vscode.commands.registerCommand("ling.addEditorSelection", async () =>
      provider.addSelection("editor"),
    ),
    vscode.commands.registerCommand("ling.addTerminalSelection", async () =>
      provider.addSelection("terminal"),
    ),
    vscode.commands.registerCommand("ling.approveDiff", async () =>
      diffController.resolveActive(true),
    ),
    vscode.commands.registerCommand("ling.denyDiff", async () =>
      diffController.resolveActive(false),
    ),
    vscode.commands.registerCommand("ling.acceptComment", async () =>
      commentPreview.accept(),
    ),
    vscode.commands.registerCommand("ling.rejectComment", async () =>
      commentPreview.reject(),
    ),
    commentPreview,
    commentController,
    commentCodeLens,
    selectionCodeLens,
    vscode.languages.registerCodeLensProvider(
      [{ scheme: "file" }],
      commentCodeLens,
    ),
    vscode.languages.registerCodeLensProvider(
      [{ scheme: "file" }, { scheme: "untitled" }],
      selectionCodeLens,
    ),
    vscode.languages.registerCodeLensProvider(
      [{ scheme: "file", language: "markdown" }],
      planCodeLens,
    ),
  );
}

export async function deactivate(): Promise<void> {
  const sidecar = activeSidecar;
  activeSidecar = undefined;
  await sidecar?.stop();
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private eventSequence = 0;
  private historyQuery = "";
  private scratchRoot: string | undefined;
  private sessionCommand: Promise<void> = Promise.resolve();
  private indexBuildPromise: Promise<IndexBuildStats> | undefined;
  private lastBuildMode: ApprovalMode = "manual";
  private readonly openedPlans = new Set<string>();
  private readonly openedPlanSessions = new Set<string>();
  private restoredLastSession = false;
  private memories: MemoryStatus[] = [];
  private memoryDiagnostics: string[] = [];
  private mcpServers: McpServerStatus[] = [];
  private mcpIssues: McpIssue[] = [];
  private mcpConfigPath = defaultMcpConfigPath();
  private readonly mcpWatcher: vscode.FileSystemWatcher;
  private mcpWatchTimer: NodeJS.Timeout | undefined;
  private controlStatusItem: vscode.StatusBarItem | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly output: vscode.OutputChannel,
    private readonly sidecar: SidecarClient,
    private readonly configPath: string,
    private settings: LingSettings,
    private readonly diffController: DiffController,
  ) {
    this.mcpWatcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(
        path.dirname(this.mcpConfigPath),
        path.basename(this.mcpConfigPath),
      ),
    );
    const refreshMcp = () => {
      if (this.mcpWatchTimer) {
        clearTimeout(this.mcpWatchTimer);
      }
      this.mcpWatchTimer = setTimeout(() => {
        this.mcpWatchTimer = undefined;
        void this.refreshMcpStatuses()
          .then(() => {
            if (this.view) {
              this.postToWebview({
                type: "ling.settings",
                settings: this.settingsView(),
              });
            }
          })
          .catch((error) => {
            this.mcpServers = [];
            this.mcpIssues = [{ message: formatError(error) }];
          });
      }, 180);
    };
    this.mcpWatcher.onDidChange(refreshMcp);
    this.mcpWatcher.onDidCreate(refreshMcp);
    this.mcpWatcher.onDidDelete(refreshMcp);
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;

    const webviewRoot = vscode.Uri.joinPath(
      this.extensionUri,
      ...WEBVIEW_ROOT,
    );
    const workspaceRoots = (
      vscode.workspace.workspaceFolders ?? []
    ).map((folder) => folder.uri);
    const localResourceRoots = [webviewRoot, ...workspaceRoots];
    try {
      const scratchRoot = vscode.Uri.file(
        this.sessionWorkspaceRoot(),
      );
      if (
        !localResourceRoots.some(
          (root) => root.fsPath === scratchRoot.fsPath,
        )
      ) {
        localResourceRoots.push(scratchRoot);
      }
    } catch (error) {
      this.output.appendLine(
        `Unable to prepare scratch workspace: ${formatError(error)}`,
      );
    }

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots,
    };

    webviewView.webview.html = this.getHtml(webviewView.webview, webviewRoot);
    webviewView.webview.onDidReceiveMessage((message: unknown) => {
      this.handleWebviewMessage(message);
    });

    webviewView.onDidDispose(() => {
      this.view = undefined;
    });
  }

  async addSelection(
    source: "any" | "editor" | "terminal" = "any",
  ): Promise<void> {
    const selection =
      source === "editor"
        ? captureEditorSelection()
        : source === "terminal"
          ? await captureTerminalSelection()
          : captureEditorSelection() ??
            (await captureTerminalSelection());

    if (!selection) {
      await vscode.window.showInformationMessage(
        "Select text in the editor or terminal first.",
      );
      return;
    }
    if (!this.view) {
      await vscode.window.showInformationMessage(
        "Open the Ling chat view first.",
      );
      return;
    }

    this.postToWebview({
      type: "ling.selection-added",
      selection: toSelectionView(selection),
    });
    await vscode.commands.executeCommand(`${CHAT_VIEW_ID}.focus`);
  }

  private handleWebviewMessage(value: unknown): void {
    if (!isWebviewToHostMessage(value)) {
      const type =
        isRecord(value) && typeof value.type === "string"
          ? value.type
          : "unknown";
      this.output.appendLine(
        `Ignored invalid Webview message: ${type}`,
      );
      this.postEvent({
        type: "run.failed",
        message:
          "The Ling UI sent an invalid request. Reload the window and try again.",
      });
      return;
    }

    switch (value.type) {
      case "ui.ready":
        if (value.protocol !== UI_PROTOCOL_VERSION) {
          this.output.appendLine(
            `UI protocol mismatch: ${value.protocol} != ${UI_PROTOCOL_VERSION}`,
          );
          this.postEvent({
            type: "run.failed",
            message: "The Ling UI and extension versions do not match.",
          });
          return;
        }

        this.output.appendLine(
          `Webview ready (${value.host}, protocol ${value.protocol})`,
        );
        void this.postHostContext();
        void this.connectSidecar();
        break;

      case "session.new":
        this.output.appendLine("New session requested");
        void this.enqueueSessionCommand(() =>
          this.handleNewSession(),
        );
        break;

      case "chat.submit":
        void this.enqueueSessionCommand(() =>
          this.handleChatSubmit(
            value.text,
            value.approvalMode,
            value.effort,
            value.attachments,
            value.selections,
          ),
        );
        break;

      case "attachment.save":
        void this.handleAttachmentSave(value);
        break;

      case "attachment.pick":
        void this.handleAttachmentPick();
        break;

      case "attachment.open":
        void this.handleAttachmentOpen(value.relativePath);
        break;

      case "selection.capture":
        void this.addSelection(value.source);
        break;

      case "selection.copy":
        void this.handleSelectionCopy(value.text);
        break;

      case "selection.open":
        void this.handleSelectionOpen(
          value.path,
          value.startLine,
          value.endLine,
        );
        break;

      case "mode.change":
        void this.handleModeChange(value.mode);
        break;

      case "settings.get":
        void this.postSettings();
        void this.refreshIndexStatus();
        break;

      case "settings.save":
        void this.handleSettingsSave(value);
        break;

      case "index.build":
        void this.handleIndexBuild();
        break;

      case "effort.change":
        void this.handleEffortChange(value.effort);
        break;

      case "approval.resolve":
        void this.handleApprovalResolve(value);
        break;

      case "question.answer":
        void this.handleQuestionAnswer(value);
        break;

      case "background.stop":
        void this.handleBackgroundStop(value.taskId);
        break;

      case "tool.background":
        void this.handleToolBackground(value.callId);
        break;

      case "control.stop":
        void this.handleControlStop();
        break;

      case "background.openLog":
        void this.handleBackgroundOpenLog(
          value.taskId,
          value.sessionId,
        );
        break;

      case "session.queued.sendNow":
        void this.enqueueSessionCommand(() =>
          this.handleQueuedSendNow(value.queuedId),
        );
        break;

      case "run.cancel":
        this.output.appendLine("Run cancellation requested");
        void this.enqueueSessionCommand(() =>
          this.handleCancelRun(),
        );
        break;

      case "message.delete":
        void this.handleDeleteMessage(value.messageId);
        break;

      case "message.regenerate":
        void this.handleRegenerateMessage(
          value.messageId,
          value.effort,
        );
        break;

      case "changes.open":
        void this.handleOpenChanges(value.sessionId, value.runId);
        break;

      case "changes.revert":
        void this.handleRevertChanges(value.sessionId, value.runId);
        break;

      case "history.list":
        void this.handleHistoryList("");
        break;

      case "history.search":
        void this.handleHistoryList(value.query);
        break;

      case "history.delete":
        void this.handleHistoryDelete(value.sessionId);
        break;

      case "history.open":
        void this.handleOpenHistory(value.sessionId);
        break;

      case "plan.open":
        void this.handlePlanOpen(value.path);
        break;

      case "plan.build":
        void this.enqueueSessionCommand(() =>
          this.handlePlanBuild(value.path),
        );
        break;

      case "memory.open":
        void this.handleMemoryOpen(value.path);
        break;

      case "memory.create":
        void this.handleMemoryCreate(value.scope);
        break;

      case "memory.delete":
        void this.handleMemoryDelete(value.scope, value.name);
        break;

      case "mcp.openConfig":
        void this.handleMcpOpenConfig();
        break;

      case "mcp.authorize":
        void this.handleMcpAuthorize(value.name);
        break;

      case "mcp.revokeAuth":
        void this.handleMcpRevokeAuth(value.name);
        break;
    }
  }

  private planFilePath(relativePath: string): string {
    const workspaceRoot = this.sessionWorkspaceRoot();
    const plansRoot = path.join(workspaceRoot, ".agents", "plans");
    const resolved = path.resolve(workspaceRoot, relativePath);
    const relative = path.relative(plansRoot, resolved);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("Plan path must be inside .agents/plans.");
    }
    return resolved;
  }

  private async handlePlanOpen(relativePath: string): Promise<void> {
    try {
      const file = this.planFilePath(relativePath);
      await vscode.window.showTextDocument(vscode.Uri.file(file), {
        preview: true,
      });
    } catch (error) {
      this.output.appendLine(
        `Unable to open plan: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Unable to open plan: ${formatError(error)}`,
      );
    }
  }

  private async handlePlanBuild(relativePath: string): Promise<void> {
    try {
      const file = this.planFilePath(relativePath);
      const raw = readFileSync(file, "utf8");
      const markdown = stripPlanFrontmatter(raw);
      if (!markdown.trim()) {
        throw new Error("The plan file is empty.");
      }
      const mode = await this.pickBuildMode();
      if (!mode) {
        return;
      }
      this.lastBuildMode = mode;
      this.postToWebview({ type: "ling.mode-changed", mode });
      await vscode.commands.executeCommand(`${CHAT_VIEW_ID}.focus`);
      const title = planTitle(markdown, relativePath);
      await this.handleChatSubmit(
        "请执行以下已批准方案。按步骤实施，用 todo_write 跟踪进度。" +
          `\n\nPlan file: ${relativePath}\n\n${markdown}`,
        mode,
        "high",
        [],
        [],
        `Build plan: ${title}`,
      );
    } catch (error) {
      this.output.appendLine(
        `Unable to build plan: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Unable to build plan: ${formatError(error)}`,
      );
    }
  }

  private async pickBuildMode(): Promise<ApprovalMode | undefined> {
    const options: Array<{
      label: string;
      description: string;
      mode: ApprovalMode;
    }> = [
      {
        label: "Manual",
        description: "Approve every edit and command",
        mode: "manual",
      },
      {
        label: "Edit automatically",
        description: "Allow edits, ask before commands",
        mode: "accept-write",
      },
      {
        label: "Auto",
        description: "Approve safe actions, ask before risky ones",
        mode: "auto",
      },
    ];
    const ordered = [...options].sort((left, right) => {
      if (left.mode === this.lastBuildMode) {
        return -1;
      }
      if (right.mode === this.lastBuildMode) {
        return 1;
      }
      return 0;
    });
    const choice = await vscode.window.showQuickPick(ordered, {
      title: "Build plan",
      placeHolder: "Choose a mode for this build",
    });
    return choice?.mode;
  }

  async buildPlanFromUri(uri: vscode.Uri): Promise<void> {
    const workspaceRoot = this.sessionWorkspaceRoot();
    const relative = path.relative(workspaceRoot, uri.fsPath);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      await vscode.window.showErrorMessage(
        "This plan is outside the active Ling workspace.",
      );
      return;
    }
    await this.enqueueSessionCommand(() =>
      this.handlePlanBuild(relative),
    );
  }

  handleSidecarExit(info: SidecarExitInfo): void {
    this.output.appendLine(
      `Sidecar stopped unexpectedly (code=${info.code ?? "null"}, signal=${info.signal ?? "null"})`,
    );
    this.updateControlStatus(null);
    this.postEvent({
      type: "run.failed",
      message: "The Ling sidecar stopped unexpectedly.",
    });
  }

  async handleControlStop(): Promise<void> {
    if (!this.sidecar.isRunning) {
      this.updateControlStatus(null);
      return;
    }
    try {
      await this.sidecar.call("computer.stop", { reason: "user" });
    } catch (error) {
      this.output.appendLine(
        `Stop computer control failed: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Unable to stop computer control: ${formatError(error)}`,
      );
    }
  }

  private updateControlStatus(
    session: ControlSessionView | null,
  ): void {
    if (session) {
      if (!this.controlStatusItem) {
        this.controlStatusItem =
          vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Left,
            100,
          );
        this.controlStatusItem.command = "ling.stopControl";
      }
      const label =
        session.app || session.windowTitle || "desktop";
      this.controlStatusItem.text =
        `$(device-desktop) Ling 正在控制 ${label}`;
      this.controlStatusItem.tooltip =
        "Click to stop computer control";
      this.controlStatusItem.show();
      void vscode.commands.executeCommand(
        "setContext",
        "ling.controlActive",
        true,
      );
      return;
    }
    this.controlStatusItem?.hide();
    void vscode.commands.executeCommand(
      "setContext",
      "ling.controlActive",
      false,
    );
  }

  handleSidecarNotification(method: string, params: unknown): void {
    if (method === "index.progress") {
      if (isIndexStatus(params)) {
        this.postToWebview({
          type: "ling.index-status",
          status: params,
        });
      }
      return;
    }
    if (
      method !== "ui.event" ||
      !isRecord(params) ||
      !isUiEvent(params.event)
    ) {
      this.output.appendLine(
        `Ignored sidecar notification: ${method} ${stringify(params)}`,
      );
      return;
    }

    if (params.event.type === "plan.created") {
      this.handlePlanCreated(
        params.event.path,
        params.event.sessionId,
      );
      return;
    }
    if (params.event.type === "computer.session.updated") {
      this.updateControlStatus(params.event.session);
    }
    if (params.event.type === "session.snapshot") {
      this.updateControlStatus(
        params.event.snapshot.computerSession ?? null,
      );
    }
    this.postEvent(params.event);
  }

  private handlePlanCreated(
    relativePath: string,
    sessionId: string,
  ): void {
    if (this.openedPlanSessions.has(sessionId)) {
      return;
    }
    if (this.openedPlans.has(relativePath)) {
      return;
    }
    this.openedPlanSessions.add(sessionId);
    this.openedPlans.add(relativePath);
    void this.openPlanPreview(relativePath);
  }

  private async openPlanPreview(relativePath: string): Promise<void> {
    try {
      const file = this.planFilePath(relativePath);
      const uri = vscode.Uri.file(file);
      try {
        await vscode.commands.executeCommand(
          "markdown.showPreview",
          uri,
        );
      } catch {
        await vscode.window.showTextDocument(uri, {
          preview: true,
          preserveFocus: true,
        });
      }
    } catch (error) {
      this.output.appendLine(
        `Unable to reveal plan: ${formatError(error)}`,
      );
    }
  }

  private async postHostContext(): Promise<void> {
    const workspaceName =
      vscode.workspace.workspaceFolders?.[0]?.name ?? "No folder";
    await this.view?.webview.postMessage({
      type: "ling.host-context",
      workspaceName,
    });
  }

  private async connectSidecar(): Promise<void> {
    try {
      const initialized = await this.sidecar.start();
      await this.sidecar.ping("webview-ready");
      this.output.appendLine(
        `Sidecar handshake complete: ${initialized.serverInfo.name} ${initialized.serverInfo.version}`,
      );

      const snapshot = await this.sidecar.call<SessionSnapshot>(
        "session.snapshot",
      );
      let restored = false;
      if (
        snapshot.entries.length === 0 &&
        snapshot.phase === "idle"
      ) {
        restored = await this.restoreLastSession();
      }
      if (!restored) {
        this.postEvent({
          type: "session.snapshot",
          snapshot,
        });
      }
      this.postToWebview({ type: "ling.ready" });
    } catch (error) {
      this.output.appendLine(
        `Sidecar handshake failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to connect to the Ling sidecar: ${formatError(error)}`,
      });
      this.postToWebview({ type: "ling.ready" });
    }
  }

  private sessionWorkspaceRoot(): string {
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (folder) {
      return folder;
    }
    if (!this.scratchRoot) {
      this.scratchRoot = resolveScratchWorkspaceRoot();
      this.output.appendLine(
        `Using scratch workspace: ${this.scratchRoot}`,
      );
    }
    return this.scratchRoot;
  }

  private async handleChatSubmit(
    text: string,
    approvalMode: ApprovalMode,
    effort: EffortLevel = "high",
    attachments: UserAttachmentRef[] = [],
    selections: UserSelectionRef[] = [],
    displayText?: string,
  ): Promise<void> {
    this.output.appendLine(
      `Chat request received (${text.length} chars, ${attachments.length} attachments, ${selections.length} selections)`,
    );
    if (!text.trim() && attachments.length === 0 && selections.length === 0) {
      this.postEvent({
        type: "run.failed",
        message: "Enter a message or attach an image.",
      });
      return;
    }

    try {
      const workspaceRoot = this.sessionWorkspaceRoot();
      const activePath =
        vscode.window.activeTextEditor?.document.uri.scheme === "file"
          ? vscode.window.activeTextEditor.document.uri.fsPath
          : undefined;
      await this.sidecar.start();
      await this.sidecar.call("session.start", {
        prompt: text,
        workspaceRoot,
        approvalMode,
        reasoning: effort,
        ...(attachments.length > 0 ? { attachments } : {}),
        ...(selections.length > 0 ? { selections } : {}),
        ...(activePath ? { activePaths: [activePath] } : {}),
        ...(displayText ? { displayText } : {}),
      });
      this.output.appendLine("Session run accepted by sidecar");
    } catch (error) {
      this.output.appendLine(
        `Session start failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to start the Ling session: ${formatError(error)}`,
      });
    }
  }

  private async handleAttachmentSave(
    value: Extract<WebviewToHostMessage, { type: "attachment.save" }>,
  ): Promise<void> {
    try {
      const workspaceRoot = this.sessionWorkspaceRoot();
      const bytes = Buffer.from(value.dataBase64, "base64");
      const saved = await saveAttachmentImage({
        workspaceRoot,
        bytes,
        mimeType: value.mimeType,
        suggestedName: value.name,
      });
      const uri = this.attachmentUri(saved.relativePath);
      this.output.appendLine(
        `Attachment saved: ${saved.relativePath} (${bytes.length} bytes)`,
      );
      this.postToWebview({
        type: "ling.attachment-saved",
        requestId: value.requestId,
        attachment: {
          ...saved,
          kind: "image",
          ...(uri ? { uri } : {}),
        },
      });
    } catch (error) {
      this.output.appendLine(
        `Attachment save failed: ${formatError(error)}`,
      );
      this.postToWebview({
        type: "ling.attachment-saved",
        requestId: value.requestId,
        error: `Unable to save image: ${formatError(error)}`,
      });
    }
  }

  private async handleAttachmentPick(): Promise<void> {
    const selection = await vscode.window.showOpenDialog({
      canSelectMany: true,
      openLabel: "Attach",
      title: "Attach files",
    });
    if (!selection?.length) {
      return;
    }

    const chosen = selection.slice(0, MAX_ATTACHMENTS_PER_MESSAGE);
    if (selection.length > chosen.length) {
      this.postToWebview({
        type: "ling.attachment-saved",
        requestId: `pick-limit-${Date.now().toString(36)}`,
        error: `Only the first ${MAX_ATTACHMENTS_PER_MESSAGE} files were attached.`,
      });
    }

    for (const [index, uri] of chosen.entries()) {
      const name = path.basename(uri.fsPath);
      try {
        const mimeType = mimeForFileName(name);
        const isImage =
          mimeType.startsWith("image/") &&
          isSupportedImageMime(mimeType);

        if (isImage) {
          const bytes = await vscode.workspace.fs.readFile(uri);
          const saved = await saveAttachmentImage({
            workspaceRoot: this.sessionWorkspaceRoot(),
            bytes,
            mimeType,
            suggestedName: name,
          });
          const webviewUri = this.attachmentUri(saved.relativePath);
          this.output.appendLine(
            `Attachment picked: ${saved.relativePath} (${bytes.length} bytes)`,
          );
          this.postToWebview({
            type: "ling.attachment-saved",
            requestId: `pick-${Date.now().toString(36)}-${index}`,
            attachment: {
              ...saved,
              kind: "image",
              ...(webviewUri ? { uri: webviewUri } : {}),
            },
          });
          continue;
        }

        const stats = await vscode.workspace.fs.stat(uri);
        if (stats.size > MAX_ATTACHMENT_FILE_BYTES) {
          throw new Error(
            `File exceeds ${MAX_ATTACHMENT_FILE_BYTES} bytes`,
          );
        }
        this.output.appendLine(
          `Attachment referenced: ${uri.fsPath} (${stats.size} bytes)`,
        );
        this.postToWebview({
          type: "ling.attachment-saved",
          requestId: `pick-${Date.now().toString(36)}-${index}`,
          attachment: {
            relativePath: uri.fsPath,
            name,
            mediaType: mimeType,
            kind: "file",
          },
        });
      } catch (error) {
        this.output.appendLine(
          `Attachment pick failed: ${formatError(error)}`,
        );
        this.postToWebview({
          type: "ling.attachment-saved",
          requestId: `pick-${Date.now().toString(36)}-${index}`,
          error: `Unable to attach ${name}: ${formatError(error)}`,
        });
      }
    }
  }

  private async handleAttachmentOpen(
    relativePath: string,
  ): Promise<void> {
    try {
      const file = path.isAbsolute(relativePath)
        ? relativePath
        : resolveAttachmentFile(
            this.sessionWorkspaceRoot(),
            relativePath,
          );
      await vscode.commands.executeCommand(
        "vscode.open",
        vscode.Uri.file(file),
      );
    } catch (error) {
      this.output.appendLine(
        `Unable to open attachment: ${formatError(error)}`,
      );
      void vscode.window.showErrorMessage(
        `Unable to open image: ${formatError(error)}`,
      );
    }
  }

  private async handleSelectionCopy(text: string): Promise<void> {
    try {
      await vscode.env.clipboard.writeText(text);
      this.output.appendLine(
        `Copied selection (${text.length} chars)`,
      );
    } catch (error) {
      this.output.appendLine(
        `Unable to copy selection: ${formatError(error)}`,
      );
      void vscode.window.showErrorMessage(
        `Unable to copy selection: ${formatError(error)}`,
      );
    }
  }

  private async handleSelectionOpen(
    filePath: string,
    startLine?: number,
    endLine?: number,
  ): Promise<void> {
    try {
      const document = await vscode.workspace.openTextDocument(
        vscode.Uri.file(filePath),
      );
      const lastLine = Math.max(0, document.lineCount - 1);
      const start = Math.min(Math.max(0, (startLine ?? 1) - 1), lastLine);
      const end = Math.min(Math.max(start, (endLine ?? start + 1) - 1), lastLine);
      const range = new vscode.Range(start, 0, end, 0);
      const editor = await vscode.window.showTextDocument(document, {
        preview: false,
      });
      editor.selection = new vscode.Selection(range.start, range.end);
      editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
    } catch (error) {
      this.output.appendLine(
        `Unable to open selection: ${formatError(error)}`,
      );
    }
  }

  private attachmentUri(relativePath: string): string | undefined {
    const view = this.view;
    if (!view) {
      return undefined;
    }
    try {
      const file = resolveAttachmentFile(
        this.sessionWorkspaceRoot(),
        relativePath,
      );
      return view.webview
        .asWebviewUri(vscode.Uri.file(file))
        .toString();
    } catch (error) {
      this.output.appendLine(
        `Unable to resolve attachment URI: ${formatError(error)}`,
      );
      return undefined;
    }
  }

  private async handleModeChange(mode: ApprovalMode): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{ mode: ApprovalMode }>(
        "session.setMode",
        { mode },
      );
      this.output.appendLine(`Approval mode set to ${result.mode}`);
    } catch (error) {
      this.output.appendLine(
        `Approval mode change failed: ${formatError(error)}`,
      );
    }
  }

  private settingsView(): SettingsView {
    const diffInline = vscode.workspace
      .getConfiguration("ling")
      .get<boolean>("diff.inlineView", true);
    return {
      apiKey: this.settings.apiKey,
      model: this.settings.model,
      baseUrl: this.settings.baseUrl,
      effort: this.settings.effort,
      diffView: diffInline ? "inline" : "sideBySide",
      maxSteps: this.settings.maxSteps,
      maxParallelTools: this.settings.maxParallelTools,
      commandTimeoutSeconds: this.settings.commandTimeoutSeconds,
      approvalTimeoutSeconds: this.settings.approvalTimeoutSeconds,
      contextWindowTokens: this.settings.contextWindowTokens,
      keepLastUserTurns: this.settings.keepLastUserTurns,
      embeddingModel: this.settings.embeddingModel,
      embeddingBaseUrl: this.settings.embeddingBaseUrl,
      embeddingApiKey: this.settings.embeddingApiKey,
      computerUseEnabled: this.settings.computerUseEnabled,
      computerSessionTtlSeconds:
        this.settings.computerSessionTtlSeconds,
      computerSessionIdleSeconds:
        this.settings.computerSessionIdleSeconds,
      computerDeniedApps: this.settings.computerDeniedApps,
      computerAuditEnabled: this.settings.computerAuditEnabled,
      skills: this.skillStatuses(),
      memories: this.memories,
      memoryDiagnostics: this.memoryDiagnostics,
      mcpServers: this.mcpServers,
      mcpIssues: this.mcpIssues,
      mcpConfigPath: this.mcpConfigPath,
    };
  }

  private skillStatuses(): SkillStatus[] {
    const roots: Array<{
      source: "builtin" | "global" | "workspace";
      root: string;
    }> = [];
    const builtin = resolveSkillsRoot(this.extensionUri.fsPath);
    if (builtin) {
      roots.push({ source: "builtin", root: builtin });
    }
    const globalRoot = resolveGlobalSkillsRoot();
    if (existsSync(globalRoot)) {
      roots.push({ source: "global", root: globalRoot });
    }
    const workspaceRoot =
      vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ??
      this.scratchRoot;
    if (workspaceRoot) {
      const skillsRoot = path.join(
        workspaceRoot,
        ".agents",
        "skills",
      );
      if (existsSync(skillsRoot)) {
        roots.push({ source: "workspace", root: skillsRoot });
      }
    }

    const found = new Map<string, SkillStatus>();
    for (const { source, root } of roots) {
      let entries: Dirent[];
      try {
        entries = readdirSync(root, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries) {
        if (!entry.isDirectory() || entry.name.startsWith(".")) {
          continue;
        }
        const skillDir = path.join(root, entry.name);
        if (!existsSync(path.join(skillDir, "SKILL.md"))) {
          continue;
        }
        if (!found.has(entry.name)) {
          found.set(entry.name, {
            name: entry.name,
            source,
            path: skillDir,
          });
        }
      }
    }
    return [...found.values()].sort((left, right) =>
      left.name.localeCompare(right.name),
    );
  }

  private async refreshIndexStatus(): Promise<void> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await this.sidecar.start();
        const status = await this.sidecar.call<IndexStatus>(
          "index.status",
          { workspaceRoot: this.sessionWorkspaceRoot() },
          15_000,
        );
        this.postToWebview({ type: "ling.index-status", status });
        return;
      } catch (error) {
        if (attempt === 0) {
          await new Promise((resolve) => setTimeout(resolve, 800));
          continue;
        }
        this.output.appendLine(
          `Unable to read code index status: ${formatError(error)}`,
        );
      }
    }
  }

  private async handleIndexBuild(): Promise<void> {
    try {
      const stats = await this.buildIndex();
      this.postToWebview({ type: "ling.index-result", ok: true, stats });
    } catch (error) {
      this.output.appendLine(
        `Code index build failed: ${formatError(error)}`,
      );
      this.postToWebview({
        type: "ling.index-result",
        ok: false,
        error: formatError(error),
      });
    }
  }

  async buildIndexFromCommand(): Promise<void> {
    try {
      const stats = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Ling: building code index",
        },
        () => this.buildIndex(),
      );
      await vscode.window.showInformationMessage(
        `Ling code index: ${stats.chunks} chunks, ` +
          `${stats.vectors} vectors (${stats.files} files)`,
      );
    } catch (error) {
      this.output.appendLine(
        `Code index build failed: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Ling code index failed: ${formatError(error)}`,
      );
    }
  }

  private buildIndex(): Promise<IndexBuildStats> {
    this.indexBuildPromise ??= this.runIndexBuild().finally(() => {
      this.indexBuildPromise = undefined;
    });
    return this.indexBuildPromise;
  }

  private async runIndexBuild(): Promise<IndexBuildStats> {
    const workspaceRoot = this.sessionWorkspaceRoot();
    this.output.appendLine(`Building code index for ${workspaceRoot}`);
    await this.sidecar.start();
    const stats = await this.sidecar.call<IndexBuildStats>(
      "index.build",
      { workspaceRoot },
      300_000,
    );
    this.output.appendLine(
      `Code index: ${stats.files} files, ${stats.chunks} chunks, ` +
        `${stats.vectors} vectors (${stats.embedded} new)` +
        (stats.error ? `; embedding failed: ${stats.error}` : ""),
    );
    return stats;
  }

  private async postSettings(error?: string): Promise<void> {
    try {
      await this.refreshMemoryStatuses();
    } catch (cause) {
      this.memoryDiagnostics = [formatError(cause)];
    }
    try {
      await this.refreshMcpStatuses();
    } catch (cause) {
      this.mcpServers = [];
      this.mcpIssues = [{ message: formatError(cause) }];
    }
    this.postToWebview({
      type: "ling.settings",
      settings: this.settingsView(),
      ...(error ? { error } : {}),
    });
  }

  private async refreshMemoryStatuses(): Promise<void> {
    await this.sidecar.start();
    const result = await this.sidecar.call<{
      memories: MemoryStatus[];
      diagnostics: string[];
    }>("memory.list", {
      workspaceRoot: this.sessionWorkspaceRoot(),
    });
    this.memories = result.memories.map((memory) => ({
      ...memory,
      globs: [...memory.globs],
    }));
    this.memoryDiagnostics = [...result.diagnostics];
  }

  private async refreshMcpStatuses(): Promise<void> {
    await this.sidecar.start();
    const result = await this.sidecar.call<{
      configPath: string;
      servers: McpServerStatus[];
      issues: McpIssue[];
    }>("mcp.status", {
      workspaceRoot: this.sessionWorkspaceRoot(),
    });
    this.mcpConfigPath = result.configPath;
    this.mcpServers = result.servers.map((server) => ({
      ...server,
      tools: [...server.tools],
    }));
    this.mcpIssues = result.issues.map((issue) => ({ ...issue }));
  }

  private async handleMcpOpenConfig(): Promise<void> {
    try {
      const result = await this.sidecar.call<{ path: string }>(
        "mcp.ensureConfig",
        { workspaceRoot: this.sessionWorkspaceRoot() },
      );
      if (
        !pathsEqual(result.path, this.mcpConfigPath) ||
        path.basename(result.path).toLowerCase() !== "mcp.json"
      ) {
        throw new Error("MCP config path is not the global config.");
      }
      await this.refreshMcpStatuses();
      this.postToWebview({
        type: "ling.settings",
        settings: this.settingsView(),
      });
      await vscode.window.showTextDocument(
        vscode.Uri.file(result.path),
        { preview: false },
      );
    } catch (error) {
      await vscode.window.showErrorMessage(
        `Unable to open MCP config: ${formatError(error)}`,
      );
    }
  }

  private async handleMcpAuthorize(name: string): Promise<void> {
    try {
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Waiting for MCP authorization: ${name}`,
        },
        () =>
          this.sidecar.call<{
            name: string;
            state: string;
            authorized: boolean;
            toolCount: number;
          }>(
            "mcp.authorize",
            {
              workspaceRoot: this.sessionWorkspaceRoot(),
              name,
            },
            300_000,
          ),
      );
      await this.postSettings();
      await vscode.window.showInformationMessage(
        `MCP server ${name} is authorized.`,
      );
    } catch (error) {
      await vscode.window.showErrorMessage(
        `Unable to authorize MCP server: ${formatError(error)}`,
      );
    }
  }

  private async handleMcpRevokeAuth(name: string): Promise<void> {
    const choice = await vscode.window.showWarningMessage(
      `Remove stored OAuth credentials for MCP server "${name}"?`,
      { modal: true },
      "Remove",
    );
    if (choice !== "Remove") {
      return;
    }
    try {
      await this.sidecar.call(
        "mcp.revokeAuth",
        {
          workspaceRoot: this.sessionWorkspaceRoot(),
          name,
        },
        30_000,
      );
      await this.postSettings();
    } catch (error) {
      await vscode.window.showErrorMessage(
        `Unable to revoke MCP authorization: ${formatError(error)}`,
      );
    }
  }

  private memoryRoot(scope: MemoryScope): string {
    if (scope === "workspace") {
      return path.join(
        this.sessionWorkspaceRoot(),
        ".agents",
        "memory",
      );
    }
    const override = process.env.LING_GLOBAL_MEMORY_ROOT?.trim();
    if (override) {
      return path.resolve(override);
    }
    const appData = process.env.APPDATA?.trim();
    return appData
      ? path.join(appData, "Ling", "memory")
      : path.join(homedir(), ".agents", "memory");
  }

  private async handleMemoryOpen(rawPath: string): Promise<void> {
    const file = path.resolve(rawPath);
    const allowed = (
      ["workspace", "global"] as MemoryScope[]
    ).some((scope) =>
      isPathInside(this.memoryRoot(scope), file),
    );
    if (!allowed || !existsSync(file)) {
      await vscode.window.showErrorMessage(
        "Unable to open memory: the file is outside the Memory folders.",
      );
      return;
    }
    await vscode.window.showTextDocument(vscode.Uri.file(file), {
      preview: true,
    });
  }

  private async handleMemoryCreate(scope: MemoryScope): Promise<void> {
    const scopeLabel = scope === "workspace" ? "project" : "global";
    const rawName = await vscode.window.showInputBox({
      title: `New ${scopeLabel} memory`,
      prompt: "Memory name",
      placeHolder: "e.g. testing-workflow",
      validateInput: (value) =>
        /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value.trim())
          ? undefined
          : "Use lowercase letters, numbers, and hyphens.",
    });
    if (rawName === undefined) {
      return;
    }
    const name = rawName.trim();
    if (
      this.memories.some(
        (memory) =>
          memory.scope === scope && memory.name === name,
      )
    ) {
      await vscode.window.showErrorMessage(
        `A ${scopeLabel} memory named ${name} already exists.`,
      );
      return;
    }
    const description = await vscode.window.showInputBox({
      title: `New ${scopeLabel} memory`,
      prompt: "Short description",
      placeHolder: "What should Ling remember?",
      validateInput: (value) =>
        value.trim() ? undefined : "Description is required.",
    });
    if (description === undefined) {
      return;
    }

    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{ path: string }>(
        "memory.write",
        {
          workspaceRoot: this.sessionWorkspaceRoot(),
          name,
          description: description.trim(),
          content: `# ${description.trim()}\n\n- `,
          scope,
          alwaysApply: true,
          globs: [],
        },
        15_000,
      );
      await this.refreshMemoryStatuses();
      this.postToWebview({
        type: "ling.settings",
        settings: this.settingsView(),
      });
      await this.handleMemoryOpen(result.path);
    } catch (error) {
      await vscode.window.showErrorMessage(
        `Unable to create memory: ${formatError(error)}`,
      );
    }
  }

  private async handleMemoryDelete(
    scope: MemoryScope,
    name: string,
  ): Promise<void> {
    const answer = await vscode.window.showWarningMessage(
      `Delete ${scope} memory "${name}"?`,
      { modal: true },
      "Delete",
    );
    if (answer !== "Delete") {
      return;
    }
    try {
      await this.sidecar.start();
      await this.sidecar.call(
        "memory.delete",
        {
          workspaceRoot: this.sessionWorkspaceRoot(),
          name,
          scope,
          confirm: true,
        },
        15_000,
      );
      await this.postSettings();
    } catch (error) {
      await vscode.window.showErrorMessage(
        `Unable to delete memory: ${formatError(error)}`,
      );
    }
  }

  private async handleSettingsSave(
    value: Extract<WebviewToHostMessage, { type: "settings.save" }>,
  ): Promise<void> {
    const model = value.model.trim();
    const baseUrl = value.baseUrl.trim().replace(/\/+$/, "");
    const embeddingModel = value.embeddingModel.trim();
    const embeddingBaseUrl = value.embeddingBaseUrl
      .trim()
      .replace(/\/+$/, "");
    const embeddingApiKey = value.embeddingApiKey.trim();
    if (!model || !baseUrl) {
      this.postToWebview({
        type: "ling.settings-saved",
        settings: this.settingsView(),
        error: "Model and API Base URL must not be empty.",
      });
      return;
    }

    const next: LingSettings = {
      version: 1,
      apiKey: value.apiKey.trim(),
      model,
      baseUrl,
      effort: value.effort,
      maxSteps: value.maxSteps,
      maxParallelTools: value.maxParallelTools,
      commandTimeoutSeconds: value.commandTimeoutSeconds,
      approvalTimeoutSeconds: value.approvalTimeoutSeconds,
      contextWindowTokens: value.contextWindowTokens,
      keepLastUserTurns: value.keepLastUserTurns,
      embeddingModel,
      embeddingBaseUrl,
      embeddingApiKey,
      computerUseEnabled: value.computerUseEnabled,
      computerSessionTtlSeconds: value.computerSessionTtlSeconds,
      computerSessionIdleSeconds: value.computerSessionIdleSeconds,
      computerDeniedApps: value.computerDeniedApps.trim(),
      computerAuditEnabled: value.computerAuditEnabled,
    };

    try {
      await writeSettings(next, this.configPath);
      this.settings = await readSettings(this.configPath);
    } catch (error) {
      this.output.appendLine(
        `Unable to save Ling settings: ${formatError(error)}`,
      );
      this.postToWebview({
        type: "ling.settings-saved",
        settings: this.settingsView(),
        error: `Unable to save settings: ${formatError(error)}`,
      });
      return;
    }

    try {
      await vscode.workspace
        .getConfiguration("ling")
        .update(
          "diff.inlineView",
          value.diffView === "inline",
          vscode.ConfigurationTarget.Global,
        );
    } catch (error) {
      this.output.appendLine(
        `Unable to update diff view setting: ${formatError(error)}`,
      );
    }

    if (this.sidecar.isRunning) {
      try {
        await this.sidecar.call("computer.stop", {
          reason: "settings",
        });
      } catch {
        // Older sidecars may not implement computer.stop.
      }
      this.updateControlStatus(null);
    }

    this.sidecar.setSettings(sidecarSettings(this.settings));
    this.output.appendLine(`Ling settings saved to ${this.configPath}`);

    try {
      await this.sidecar.restart();
      await this.sidecar.ping("settings-updated");
      this.postToWebview({
        type: "ling.settings-saved",
        settings: this.settingsView(),
      });
    } catch (error) {
      this.output.appendLine(
        `Sidecar restart after settings change failed: ${formatError(error)}`,
      );
      this.postToWebview({
        type: "ling.settings-saved",
        settings: this.settingsView(),
        warning: `Settings saved, but the sidecar could not restart: ${formatError(error)}`,
      });
    }
  }

  private async handleEffortChange(
    effort: EffortLevel,
  ): Promise<void> {
    if (this.settings.effort === effort) {
      return;
    }
    try {
      await writeSettings(
        { ...this.settings, effort },
        this.configPath,
      );
      this.settings = { ...this.settings, effort };
      this.sidecar.setSettings(sidecarSettings(this.settings));
      this.output.appendLine(`Reasoning effort set to ${effort}`);
    } catch (error) {
      this.output.appendLine(
        `Unable to save reasoning effort: ${formatError(error)}`,
      );
    }
  }

  private async handleDeleteMessage(
    messageId: string,
  ): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("session.deleteMessage", { messageId });
    } catch (error) {
      this.output.appendLine(
        `Unable to delete message: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to delete the message: ${formatError(error)}`,
      });
    }
  }

  private async handleRegenerateMessage(
    messageId: string,
    effort?: EffortLevel,
  ): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("session.regenerate", {
        messageId,
        ...(effort ? { reasoning: effort } : {}),
      });
    } catch (error) {
      this.output.appendLine(
        `Unable to regenerate message: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to regenerate the message: ${formatError(error)}`,
      });
    }
  }

  private async handleNewSession(): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("session.new");
    } catch (error) {
      this.output.appendLine(`New session failed: ${formatError(error)}`);
      this.postEvent({
        type: "run.failed",
        message: `Unable to start a new session: ${formatError(error)}`,
      });
    }
  }

  private async handleOpenChanges(
    sessionId: string,
    runId: string,
  ): Promise<void> {
    try {
      const result = await this.sidecar.call<{
        runId: string;
        changes: ChangeReviewItem[];
      }>("changes.get", { sessionId, runId });
      if (result.changes.length === 0) {
        throw new Error("No recorded changes for this run.");
      }
      await this.diffController.openChanges(
        runId,
        `Ling: 本轮修改（${result.changes.length} 个文件）`,
        result.changes,
      );
    } catch (error) {
      this.output.appendLine(
        `Unable to open run changes: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Unable to open run changes: ${formatError(error)}`,
      );
    }
  }

  private async handleRevertChanges(
    sessionId: string,
    runId: string,
  ): Promise<void> {
    const choice = await vscode.window.showWarningMessage(
      "撤销这一轮对工作区文件的修改？",
      {
        modal: true,
        detail:
          "Ling 会先检查文件是否仍保持本轮结束时的状态；如已被后续修改，则不会覆盖。",
      },
      "撤销",
    );
    if (choice !== "撤销") {
      return;
    }
    try {
      const result = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "正在撤销 Ling 修改…",
        },
        () =>
          this.sidecar.call<{ reverted: boolean; count: number }>(
            "changes.revert",
            { sessionId, runId },
          ),
      );
      await vscode.window.showInformationMessage(
        `已撤销 ${result.count} 个文件的修改。`,
      );
    } catch (error) {
      this.output.appendLine(
        `Unable to revert run changes: ${formatError(error)}`,
      );
      await vscode.window.showErrorMessage(
        `Unable to revert changes: ${formatError(error)}`,
      );
    }
  }

  private async handleCancelRun(): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("session.cancel");
    } catch (error) {
      this.output.appendLine(`Run cancellation failed: ${formatError(error)}`);
      this.postEvent({
        type: "run.failed",
        message: `Unable to cancel the run: ${formatError(error)}`,
      });
    }
  }

  private enqueueSessionCommand<T>(
    command: () => Promise<T>,
  ): Promise<T> {
    const result = this.sessionCommand.then(command);
    this.sessionCommand = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async handleApprovalResolve(
    message: Extract<WebviewToHostMessage, { type: "approval.resolve" }>,
  ): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{ resolved: boolean }>(
        "approval.resolve",
        {
          approvalId: message.approvalId,
          effectDigest: message.effectDigest,
          approved: message.approved,
          ...(message.remember ? { remember: true } : {}),
          ...(message.feedback ? { feedback: message.feedback } : {}),
        },
      );
      if (!result.resolved) {
        throw new Error("The approval is no longer available");
      }
    } catch (error) {
      this.output.appendLine(
        `Approval resolution failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to resolve approval: ${formatError(error)}`,
      });
    }
  }

  private async handleBackgroundStop(taskId: string): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("background.stop", { taskId });
    } catch (error) {
      this.output.appendLine(
        `Background task stop failed: ${formatError(error)}`,
      );
    }
  }

  private async handleToolBackground(callId: string): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{
        task: BackgroundTaskView;
      }>("tool.background", { callId });
      this.postToWebview({
        type: "ling.background-result",
        callId,
        accepted: true,
        task: result.task,
      });
    } catch (error) {
      const message = formatError(error);
      this.output.appendLine(
        `Moving tool call to the background failed: ${message}`,
      );
      this.postToWebview({
        type: "ling.background-result",
        callId,
        accepted: false,
        error: message,
      });
    }
  }

  private async handleBackgroundOpenLog(
    taskId: string,
    sessionId?: string,
  ): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{
        tasks: BackgroundTaskView[];
      }>(
        "background.list",
        sessionId ? { sessionId } : {},
      );
      const task = result.tasks.find((item) => item.id === taskId);
      if (!task) {
        throw new Error("Background task not found");
      }
      const outputPath = task.outputPath;
      if (!outputPath) {
        throw new Error("This task has no log file");
      }
      const workspaceRoot = this.sessionWorkspaceRoot();
      const logRoot = path.join(
        workspaceRoot,
        ".agents",
        "background",
      );
      const resolved = path.isAbsolute(outputPath)
        ? path.resolve(outputPath)
        : path.resolve(workspaceRoot, outputPath);
      const relative = path.relative(logRoot, resolved);
      if (
        relative.startsWith("..") ||
        path.isAbsolute(relative)
      ) {
        throw new Error("The log path is outside the workspace");
      }
      if (!existsSync(resolved)) {
        throw new Error("The log file no longer exists");
      }
      await vscode.window.showTextDocument(
        vscode.Uri.file(resolved),
        { preview: true },
      );
    } catch (error) {
      const message = formatError(error);
      this.output.appendLine(
        `Opening the background task log failed: ${message}`,
      );
      void vscode.window.showErrorMessage(message);
    }
  }

  private async handleQuestionAnswer(
    message: Extract<WebviewToHostMessage, { type: "question.answer" }>,
  ): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{ resolved: boolean }>(
        "question.answer",
        {
          callId: message.callId,
          ...(message.answers ? { answers: message.answers } : {}),
          cancelled: message.cancelled === true,
        },
      );
      if (!result.resolved) {
        this.output.appendLine(
          `Question ${message.callId} is no longer pending`,
        );
      }
    } catch (error) {
      this.output.appendLine(
        `Question answer failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to answer the question: ${formatError(error)}`,
      });
    }
  }

  private async handleQueuedSendNow(queuedId: string): Promise<void> {
    try {
      await this.sidecar.start();
      const result = await this.sidecar.call<{ accepted: boolean }>(
        "session.queued.sendNow",
        { queuedId },
      );
      if (!result.accepted) {
        this.output.appendLine(
          `Queued message ${queuedId} is no longer available`,
        );
      }
    } catch (error) {
      this.output.appendLine(
        `Queued message send failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to send the queued message: ${formatError(error)}`,
      });
    }
  }

  private async handleHistoryList(
    query?: string,
    errorMessage?: string,
  ): Promise<void> {
    if (query !== undefined) {
      this.historyQuery = query;
    }

    try {
      await this.sidecar.start();
      const workspaceRoot = this.sessionWorkspaceRoot();
      const result = this.historyQuery
        ? await this.sidecar.call<{
            sessions: HistorySession[];
          }>("session.search", {
            query: this.historyQuery,
            workspaceRoot,
          })
        : await this.sidecar.call<{
            sessions: HistorySession[];
          }>("session.list", { workspaceRoot });
      await this.view?.webview.postMessage({
        type: "ling.history-list",
        sessions: result.sessions,
        ...(errorMessage ? { error: errorMessage } : {}),
      });
    } catch (error) {
      this.output.appendLine(
        `Unable to load conversation history: ${formatError(error)}`,
      );
      await this.view?.webview.postMessage({
        type: "ling.history-list",
        sessions: [],
        error:
          errorMessage ??
          `Unable to load conversation history: ${formatError(error)}`,
      });
    }
  }

  private async handleHistoryDelete(sessionId: string): Promise<void> {
    try {
      await this.sidecar.start();
      await this.sidecar.call("session.delete", { sessionId });
      this.output.appendLine(`Deleted conversation ${sessionId}`);
    } catch (error) {
      this.output.appendLine(
        `Unable to delete conversation: ${formatError(error)}`,
      );
      await this.handleHistoryList(
        undefined,
        `Unable to delete conversation: ${formatError(error)}`,
      );
      return;
    }

    await this.handleHistoryList();
  }

  private async restoreLastSession(): Promise<boolean> {
    if (this.restoredLastSession) {
      return false;
    }
    this.restoredLastSession = true;
    try {
      const workspaceRoot = this.sessionWorkspaceRoot();
      const result = await this.sidecar.call<{
        sessions: HistorySession[];
      }>("session.list", { workspaceRoot });
      const latest = result.sessions[0];
      if (!latest) {
        return false;
      }
      const snapshot = await this.sidecar.call<SessionSnapshot>(
        "session.open",
        { sessionId: latest.id, workspaceRoot },
      );
      this.postEvent({ type: "session.snapshot", snapshot });
      this.output.appendLine(
        `Restored last conversation: ${latest.id}`,
      );
      return true;
    } catch (error) {
      this.output.appendLine(
        `Unable to restore last conversation: ${formatError(error)}`,
      );
      return false;
    }
  }

  private async handleOpenHistory(sessionId: string): Promise<void> {
    try {
      await this.sidecar.start();
      const workspaceRoot = this.sessionWorkspaceRoot();
      const snapshot = await this.sidecar.call<SessionSnapshot>(
        "session.open",
        { sessionId, workspaceRoot },
      );
      this.postEvent({
        type: "session.snapshot",
        snapshot,
      });
    } catch (error) {
      this.output.appendLine(
        `Unable to open conversation: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to open conversation: ${formatError(error)}`,
      });
    }
  }

  private postToWebview(message: HostToWebviewMessage): void {
    void this.view?.webview.postMessage(message);
  }

  private postEvent(event: UiEvent): void {
    if (!this.view) {
      return;
    }

    if (event.type === "session.snapshot") {
      this.diffController.update(event.snapshot);
    }

    const enriched = this.withAttachmentUris(event);
    void this.view.webview
      .postMessage({
        type: "ling.ui-event",
        sequence: ++this.eventSequence,
        event: enriched,
      })
      .then((delivered) => {
        if (!delivered) {
          this.output.appendLine(
            `Unable to deliver UI event: ${event.type}`,
          );
        }
      });
  }

  private withAttachmentUris(event: UiEvent): UiEvent {
    if (event.type !== "session.snapshot") {
      return event;
    }
    if (!this.view) {
      return event;
    }

    let changed = false;
    const entries = event.snapshot.entries.map((entry) => {
      if (entry.kind !== "message") {
        return entry;
      }
      const current = entry.message.attachments;
      if (!current?.length) {
        return entry;
      }

      const attachments = current.map((attachment) => {
        if (attachment.uri) {
          return attachment;
        }
        const uri = this.attachmentUri(attachment.relativePath);
        return uri ? { ...attachment, uri } : attachment;
      });
      if (
        attachments.every(
          (attachment, index) => attachment === current[index],
        )
      ) {
        return entry;
      }

      changed = true;
      return {
        ...entry,
        message: {
          ...entry.message,
          attachments,
        },
      };
    });

    if (!changed) {
      return event;
    }
    return {
      ...event,
      snapshot: {
        ...event.snapshot,
        entries,
      },
    };
  }

  private getHtml(webview: vscode.Webview, webviewRoot: vscode.Uri): string {
    const indexPath = vscode.Uri.joinPath(webviewRoot, "index.html");
    const source = readFileSync(indexPath.fsPath, "utf8");
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} data:`,
      `font-src ${webview.cspSource}`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src ${webview.cspSource}`,
      "frame-src 'self' data: blob:",
    ].join("; ");

    const withLocalAssets = source.replace(
      /(src|href)="\.?\/?(assets\/[^"]+)"/g,
      (_match, attribute: string, assetPath: string) => {
        const assetUri = webview.asWebviewUri(
          vscode.Uri.joinPath(webviewRoot, ...assetPath.split("/")),
        );
        return `${attribute}="${assetUri}"`;
      },
    );

    return withLocalAssets.replace(
      "<head>",
      `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp}">`,
    );
  }
}

function stringify(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function planTitle(markdown: string, relativePath: string): string {
  const heading = markdown.match(/^\s{0,3}#{1,6}\s+(.+)$/m);
  const raw = heading
    ? heading[1].trim()
    : (relativePath.split("/").pop() ?? relativePath).replace(
        /\.md$/i,
        "",
      );
  return raw.length > 80 ? `${raw.slice(0, 77)}...` : raw;
}

function stripPlanFrontmatter(text: string): string {
  const normalized = text.replace(/^﻿/, "");
  if (
    !normalized.startsWith("---\n") &&
    !normalized.startsWith("---\r\n")
  ) {
    return normalized;
  }
  const closing = normalized.indexOf("\n---", 4);
  if (closing < 0) {
    return normalized;
  }
  return normalized.slice(closing + 4).replace(/^\s+/, "");
}

function pathsEqual(left: string, right: string): boolean {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === "win32"
    ? a.toLowerCase() === b.toLowerCase()
    : a === b;
}

function isPathInside(root: string, candidate: string): boolean {
  const relative = path.relative(
    path.resolve(root),
    path.resolve(candidate),
  );
  return (
    relative === "" ||
    (!relative.startsWith("..") && !path.isAbsolute(relative))
  );
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
