import * as vscode from "vscode";

export class SelectionCodeLensProvider
  implements vscode.CodeLensProvider, vscode.Disposable
{
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this.changed.event;
  private readonly listeners: vscode.Disposable[];
  private activeUri: string | undefined;
  private line = 0;
  private lineCount = 0;

  constructor() {
    this.listeners = [
      vscode.window.onDidChangeTextEditorSelection((event) =>
        this.updateFromEditor(event.textEditor),
      ),
      vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor) {
          this.updateFromEditor(editor);
        } else {
          this.clear();
        }
      }),
    ];
  }

  private updateFromEditor(editor: vscode.TextEditor): void {
    const selection = editor.selection;
    if (selection.isEmpty) {
      this.clear();
      return;
    }

    const startLine = selection.start.line;
    let endLine = selection.end.line;
    if (selection.end.character === 0 && endLine > startLine) {
      endLine -= 1;
    }
    this.activeUri = editor.document.uri.toString();
    this.line = startLine;
    this.lineCount = Math.max(1, endLine - startLine + 1);
    this.changed.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    if (
      !this.activeUri ||
      document.uri.toString() !== this.activeUri
    ) {
      return [];
    }
    return [
      new vscode.CodeLens(
        new vscode.Range(this.line, 0, this.line, 0),
        {
          title: `Add ${this.lineCount} ${
            this.lineCount === 1 ? "line" : "lines"
          } to Ling chat`,
          command: "ling.addEditorSelection",
        },
      ),
    ];
  }

  dispose(): void {
    for (const listener of this.listeners) {
      listener.dispose();
    }
    this.changed.dispose();
  }

  private clear(): void {
    if (this.activeUri === undefined) {
      return;
    }
    this.activeUri = undefined;
    this.line = 0;
    this.lineCount = 0;
    this.changed.fire();
  }
}
