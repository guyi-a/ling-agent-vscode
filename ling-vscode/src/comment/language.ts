const PYTHON_LANGUAGES = new Set(["python"]);
const JS_LANGUAGES = new Set([
  "typescript",
  "javascript",
  "typescriptreact",
  "javascriptreact",
]);

export function isPythonLanguage(languageId: string): boolean {
  return PYTHON_LANGUAGES.has(languageId);
}

export function isJsLanguage(languageId: string): boolean {
  return JS_LANGUAGES.has(languageId);
}

export function isSupportedCommentLanguage(
  languageId: string,
): boolean {
  return isPythonLanguage(languageId) || isJsLanguage(languageId);
}

export interface TextDocumentLike {
  lineCount: number;
  lineAt(line: number): { text: string };
}

export function leadingJsDocStart(
  document: TextDocumentLike,
  startLine: number,
): number | undefined {
  let line = startLine - 1;
  while (line >= 0) {
    const text = document.lineAt(line).text.trim();
    if (!text) {
      return undefined;
    }
    if (text.startsWith("@")) {
      line -= 1;
      continue;
    }
    if (!text.endsWith("*/")) {
      return undefined;
    }
    for (let index = line; index >= 0; index -= 1) {
      const inner = document.lineAt(index).text.trim();
      if (inner.startsWith("/**")) {
        return index;
      }
      if (inner.startsWith("/*")) {
        return undefined;
      }
    }
    return undefined;
  }
  return undefined;
}

export function hasLeadingJsDoc(
  document: TextDocumentLike,
  startLine: number,
): boolean {
  return leadingJsDocStart(document, startLine) !== undefined;
}
