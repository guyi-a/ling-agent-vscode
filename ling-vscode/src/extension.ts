import {
  existsSync,
  readFileSync,
  readdirSync,
  type Dirent,
} from "node:fs";
import path from "node:path";
import * as vscode from "vscode";
import { resolveScratchWorkspaceRoot } from "./scratchWorkspace";
import {
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
  EffortLevel,
  HistorySession,
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
import { DiffController } from "./diffController";
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

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly output: vscode.OutputChannel,
    private readonly sidecar: SidecarClient,
    private readonly configPath: string,
    private settings: LingSettings,
    private readonly diffController: DiffController,
  ) {}

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
        void this.handleNewSession();
        break;

      case "chat.submit":
        void this.handleChatSubmit(
          value.text,
          value.approvalMode,
          value.effort,
          value.attachments,
          value.selections,
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
        this.postSettings();
        break;

      case "settings.save":
        void this.handleSettingsSave(value);
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

      case "session.queued.sendNow":
        void this.handleQueuedSendNow(value.queuedId);
        break;

      case "run.cancel":
        this.output.appendLine("Run cancellation requested");
        void this.handleCancelRun();
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
    }
  }

  handleSidecarExit(info: SidecarExitInfo): void {
    this.output.appendLine(
      `Sidecar stopped unexpectedly (code=${info.code ?? "null"}, signal=${info.signal ?? "null"})`,
    );
    this.postEvent({
      type: "run.failed",
      message: "The Ling sidecar stopped unexpectedly.",
    });
  }

  handleSidecarNotification(method: string, params: unknown): void {
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

    this.postEvent(params.event);
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
      this.postEvent({
        type: "session.snapshot",
        snapshot,
      });
    } catch (error) {
      this.output.appendLine(
        `Sidecar handshake failed: ${formatError(error)}`,
      );
      this.postEvent({
        type: "run.failed",
        message: `Unable to connect to the Ling sidecar: ${formatError(error)}`,
      });
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
      await this.sidecar.start();
      await this.sidecar.call("session.start", {
        prompt: text,
        workspaceRoot,
        approvalMode,
        reasoning: effort,
        ...(attachments.length > 0 ? { attachments } : {}),
        ...(selections.length > 0 ? { selections } : {}),
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
      skills: this.skillStatuses(),
    };
  }

  private skillStatuses(): SkillStatus[] {
    const roots: Array<{
      source: "builtin" | "workspace";
      root: string;
    }> = [];
    const builtin = resolveSkillsRoot(this.extensionUri.fsPath);
    if (builtin) {
      roots.push({ source: "builtin", root: builtin });
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
        const existing = found.get(entry.name);
        if (existing && source === "workspace") {
          found.set(entry.name, {
            name: entry.name,
            source,
            path: skillDir,
            overridesBuiltin: true,
          });
        } else if (!existing) {
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

  private postSettings(error?: string): void {
    this.postToWebview({
      type: "ling.settings",
      settings: this.settingsView(),
      ...(error ? { error } : {}),
    });
  }

  private async handleSettingsSave(
    value: Extract<WebviewToHostMessage, { type: "settings.save" }>,
  ): Promise<void> {
    const model = value.model.trim();
    const baseUrl = value.baseUrl.trim().replace(/\/+$/, "");
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

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
