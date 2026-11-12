import assert from "node:assert/strict";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import remarkGfm from "remark-gfm";
import {
  remarkCjkAutolink,
  remarkRendererFence,
  rendererCodeBlock,
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
assert.equal(
  rendererCodeBlock(
    '<svg viewBox="0 0 10 10"><rect width="5" height="5" /></svg>\n',
  ),
  [
    '```html type="renderer"',
    '<svg viewBox="0 0 10 10"><rect width="5" height="5" /></svg>',
    "```",
  ].join("\n"),
);

const linkProcessor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkCjkAutolink);
const linkTree = linkProcessor.runSync(
  linkProcessor.parse(
    "抖音已在新标签页打开（https://www.douyin.com/），页面正常加载。",
  ),
);
let linkNode: any;
visitNodes(linkTree, (node: any) => {
  if (node.type === "link") {
    linkNode = node;
  }
});
assert.ok(linkNode);
assert.equal(linkNode.url, "https://www.douyin.com/");
assert.equal(linkNode.children[0].value, "https://www.douyin.com/");
const paragraph = linkTree.children[0];
const linkIndex = paragraph.children.findIndex(
  (node: any) => node.type === "link",
);
assert.equal(
  paragraph.children[linkIndex + 1].value,
  "），页面正常加载。",
);

function visitNodes(node: any, callback: (node: any) => void): void {
  callback(node);
  for (const child of node.children ?? []) {
    visitNodes(child, callback);
  }
}

console.log("inline renderer markdown tests passed");
