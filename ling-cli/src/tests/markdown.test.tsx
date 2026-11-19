import assert from "node:assert/strict";
import test from "node:test";

import { render } from "ink-testing-library";

import { Markdown } from "../tui/markdown.js";

function outputOf(text: string, width: number): string {
  const instance = render(<Markdown text={text} width={width} />);
  const output = instance.lastFrame() ?? "";
  instance.unmount();
  return output;
}

test("replaces renderer code blocks with a placeholder", () => {
  const output = outputOf(
    '```html type="renderer"\n<svg><rect width="10" /></svg>\n```',
    60,
  );
  assert.match(output, /inline visualization/);
  assert.match(output, /1 line/);
  assert.doesNotMatch(output, /<svg>/);
  assert.doesNotMatch(output, /<rect/);
});

test("keeps ordinary code blocks as source", () => {
  const output = outputOf("```html\n<div>hello</div>\n```", 60);
  assert.match(output, /<div>hello<\/div>/);
  assert.doesNotMatch(output, /inline visualization/);
});

test("wraps CJK list text without dropping a character to column 0", () => {
  const text =
    "- 原生 diff 审批；四种模式：Manual / Edit automatically / Auto / Plan（只读探索，计划写到 `.agents/plans/` 等你说 Build）";
  const output = outputOf(text, 40);
  const lines = output.split("\n").filter((line) => line.trim().length > 0);
  assert.ok(lines.length > 1, output);
  assert.match(lines[0], /^• /);
  for (const line of lines.slice(1)) {
    assert.match(
      line,
      /^ {2}\S/,
      `continuation line is not indented: ${JSON.stringify(line)}`,
    );
  }
  assert.ok(
    !lines.some((line) => /^\s*说\s*$/.test(line)),
    `orphan character found in:\n${output}`,
  );
});
