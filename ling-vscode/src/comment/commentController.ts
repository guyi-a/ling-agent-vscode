import * as vscode from "vscode";
import { isCommentGeneration } from "../../shared/commentTypes";
import type { SidecarClient } from "../sidecar/SidecarClient";
import { CommentPreviewController } from "./commentPreview";
import {
  CommentTargetError,
  collectCommentTarget,
} from "./targetCollector";

const GENERATE_TIMEOUT_MS = 120_000;

interface CommentCommandTarget {
  uri: string;
  line: number;
}

export class CommentController implements vscode.Disposable {
  private readonly disposables: vscode.Disposable[];

  constructor(
    private readonly sidecar: SidecarClient,
    private readonly preview: CommentPreviewController,
    private readonly output: vscode.OutputChannel,
  ) {
    this.disposables = [
      vscode.commands.registerCommand(
        "ling.generateComment",
        (target?: unknown) => this.generate(target),
      ),
    ];
  }

  dispose(): void {
    for (const disposable of this.disposables) {
      disposable.dispose();
    }
  }

  private async generate(rawTarget?: unknown): Promise<void> {
    try {
      const resolved = await resolveCommandTarget(rawTarget);
      if (!resolved) {
        void vscode.window.showWarningMessage(
          "Open a Python file first.",
        );
        return;
      }
      const { document, position } = resolved;
      const collected = await collectCommentTarget(
        document,
        position,
      );
      const generation = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Generating comment…",
          cancellable: false,
        },
        async () => {
          await this.sidecar.start();
          const result = await this.sidecar.call<unknown>(
            "comment.generate",
            { ...collected.target },
            GENERATE_TIMEOUT_MS,
          );
          if (!isCommentGeneration(result)) {
            throw new Error(
              "The sidecar returned an invalid comment response.",
            );
          }
          return result;
        },
      );

      await this.preview.show(
        document,
        collected.target,
        generation,
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error);
      if (error instanceof CommentTargetError) {
        void vscode.window.showWarningMessage(message);
        return;
      }
      this.output.appendLine(`Generate comment failed: ${message}`);
      void vscode.window.showErrorMessage(
        `Generate comment failed: ${message}`,
      );
    }
  }
}

async function resolveCommandTarget(
  rawTarget: unknown,
): Promise<
  | { document: vscode.TextDocument; position: vscode.Position }
  | undefined
> {
  const target = toCommandTarget(rawTarget);
  if (target) {
    const uri = vscode.Uri.parse(target.uri);
    const document =
      vscode.workspace.textDocuments.find(
        (item) => item.uri.toString() === uri.toString(),
      ) ?? (await vscode.workspace.openTextDocument(uri));
    return {
      document,
      position: new vscode.Position(target.line, 0),
    };
  }

  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return undefined;
  }
  return {
    document: editor.document,
    position: editor.selection.active,
  };
}

function toCommandTarget(
  value: unknown,
): CommentCommandTarget | undefined {
  if (typeof value !== "object" || value === null) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.uri !== "string" ||
    typeof record.line !== "number" ||
    !Number.isInteger(record.line) ||
    record.line < 0
  ) {
    return undefined;
  }
  return { uri: record.uri, line: record.line };
}
