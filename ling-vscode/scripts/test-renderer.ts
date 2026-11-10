import assert from "node:assert/strict";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import {
  remarkRendererFence,
  rendererContentFromPre,
} from "../webview/src/components/markdownRenderer";

function toHast(markdown: string): any {
  const processor = unified().use(remarkParse).use(remarkRendererFence);
  const mdast = processor.parse(markdown);
  processor.runSync(mdast);
  return unified().use(remarkRehype).runSync(mdast);
}

const document = toHast(
  [
    '```html type="renderer"',
    '<svg viewBox="0 0 10 10"><rect width="5" height="5" /></svg>',
    "```",
    "",
    "```html",
    "<div>source only</div>",
    "```",
  ].join("\n"),
);

const root = document as any;
const preNodes = (root.children ?? []).filter(
  (node: any) => node.tagName === "pre",
);
assert.equal(preNodes.length, 2);
assert.equal(
  rendererContentFromPre(preNodes[0]),
  '<svg viewBox="0 0 10 10"><rect width="5" height="5" /></svg>\n',
);
assert.equal(rendererContentFromPre(preNodes[1]), undefined);

const partial = toHast(
  '```html type=renderer\n<svg viewBox="0 0 10 10"><rect width="5"',
);
const partialPre = (partial as any).children.find(
  (node: any) => node.tagName === "pre",
);
assert.equal(
  rendererContentFromPre(partialPre),
  '<svg viewBox="0 0 10 10"><rect width="5"\n',
);

console.log("inline renderer markdown tests passed");
