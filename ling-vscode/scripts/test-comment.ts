import assert from "node:assert/strict";
import {
  applyComment,
  formatCommentBlock,
  insertAtLine,
} from "../src/comment/format";
import {
  hasLeadingJsDoc,
  isJsLanguage,
  isPythonLanguage,
  isSupportedCommentLanguage,
  leadingJsDocStart,
} from "../src/comment/language";
import { isCommentGeneration } from "../shared/commentTypes";

const generation = {
  comment: '"""Adds two numbers."""',
  placement: { line: 9, indent: "    " },
};
assert.equal(isCommentGeneration(generation), true);
assert.equal(
  isCommentGeneration({
    comment: "",
    placement: { line: 0, indent: "" },
  }),
  false,
);
assert.equal(
  isCommentGeneration({
    comment: '"""Adds two numbers."""',
    placement: { line: -1, indent: "" },
  }),
  false,
);
assert.equal(
  isCommentGeneration({
    comment: '"""Adds two numbers."""',
    placement: { line: 0, indent: 4 },
  }),
  false,
);
assert.equal(isCommentGeneration(null), false);
assert.equal(isCommentGeneration({ comment: "x" }), false);
assert.equal(
  isCommentGeneration({
    comment: '"""x"""',
    placement: { line: 1, indent: "", endLine: 3 },
  }),
  true,
);
assert.equal(
  isCommentGeneration({
    comment: '"""x"""',
    placement: { line: 1, indent: "", endLine: 1 },
  }),
  false,
);
assert.equal(
  isCommentGeneration({
    comment: '"""x"""',
    placement: { line: 1, indent: "", endLine: 0 },
  }),
  false,
);

assert.equal(
  formatCommentBlock(
    '"""Line one.\n\nLine two."""',
    "    ",
    "\n",
  ),
  '    """Line one.\n\n    Line two."""\n',
);
assert.equal(
  formatCommentBlock('"""Method."""', "        ", "\r\n"),
  '        """Method."""\r\n',
);

assert.equal(
  insertAtLine(
    "def calc():\n    return 1\n",
    1,
    '    """Adds one."""\n',
  ),
  'def calc():\n    """Adds one."""\n    return 1\n',
);
assert.equal(
  insertAtLine(
    "def calc():\r\n    return 1\r\n",
    1,
    '    """Adds one."""\r\n',
  ),
  'def calc():\r\n    """Adds one."""\r\n    return 1\r\n',
);
assert.equal(
  insertAtLine("def calc():\n    return 1", 1, '    """x"""\n'),
  'def calc():\n    """x"""\n    return 1',
);
assert.equal(
  applyComment(
    'def calc():\n    """Old."""\n    return 1\n',
    { line: 1, indent: "    ", endLine: 2 },
    '    """New."""\n',
  ),
  'def calc():\n    """New."""\n    return 1\n',
);
assert.equal(
  applyComment(
    "line0\nline1\nline2\n",
    { line: 1, indent: "", endLine: 3 },
    "new\n",
  ),
  "line0\nnew\n",
);

assert.equal(isPythonLanguage("python"), true);
assert.equal(isJsLanguage("typescript"), true);
assert.equal(isJsLanguage("typescriptreact"), true);
assert.equal(isJsLanguage("javascriptreact"), true);
assert.equal(isSupportedCommentLanguage("python"), true);
assert.equal(isSupportedCommentLanguage("typescript"), true);
assert.equal(isSupportedCommentLanguage("go"), false);

function documentOf(lines: string[]) {
  return {
    lineCount: lines.length,
    lineAt(line: number) {
      return { text: lines[line] ?? "" };
    },
  };
}

assert.equal(
  hasLeadingJsDoc(
    documentOf(["/** Adds. */", "export function add() {}"]),
    1,
  ),
  true,
);
assert.equal(
  hasLeadingJsDoc(
    documentOf([
      "/**",
      " * Adds.",
      " */",
      "export function add() {}",
    ]),
    3,
  ),
  true,
);
assert.equal(
  hasLeadingJsDoc(
    documentOf([
      "/** Adds. */",
      "@decorator",
      "run() {}",
    ]),
    2,
  ),
  true,
);
assert.equal(
  hasLeadingJsDoc(
    documentOf(["/* note */", "function add() {}"]),
    1,
  ),
  false,
);
assert.equal(
  hasLeadingJsDoc(
    documentOf(["// note", "function add() {}"]),
    1,
  ),
  false,
);
assert.equal(
  hasLeadingJsDoc(
    documentOf(["", "function add() {}"]),
    1,
  ),
  false,
);
assert.equal(
  leadingJsDocStart(
    documentOf(["/** Adds. */", "export function add() {}"]),
    1,
  ),
  0,
);
assert.equal(
  leadingJsDocStart(
    documentOf([
      "/**",
      " * Adds.",
      " */",
      "export function add() {}",
    ]),
    3,
  ),
  0,
);
assert.equal(
  leadingJsDocStart(
    documentOf([
      "/** Adds. */",
      "@decorator",
      "run() {}",
    ]),
    2,
  ),
  0,
);
assert.equal(
  leadingJsDocStart(
    documentOf(["const x = 1;", "function add() {}"]),
    1,
  ),
  undefined,
);

console.log("comment tests passed");
