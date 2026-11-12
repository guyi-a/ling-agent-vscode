import { visit as unistVisit } from "unist-util-visit";

const CJK_STOP = /[　-〿㐀-鿿豈-﫿＀-￯]/u;

interface MarkdownNode {
  type?: string;
  lang?: string;
  meta?: string;
  value?: string;
  data?: {
    hProperties?: Record<string, unknown>;
  };
  children?: MarkdownNode[];
}

interface HastNode {
  type?: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

const RENDERER_META = /(?:^|\s)type\s*=\s*["']?renderer["']?(?=\s|$)/i;

export function remarkRendererFence() {
  return (tree: MarkdownNode): void => {
    visit(tree);
  };
}

/**
 * GFM autolinks swallow trailing CJK punctuation and prose. Split such
 * links so "https://example.com），说明" keeps the explanation outside the
 * anchor instead of colouring and linking the rest of the sentence.
 */
export function remarkCjkAutolink() {
  return (tree: unknown): void => {
    unistVisit(
      tree as any,
      "link",
      (node: any, index: number | undefined, parent: any) => {
      if (
        index === undefined ||
        !parent ||
        typeof node.url !== "string" ||
        !/^https?:\/\//i.test(node.url)
      ) {
        return;
      }
      const children = node.children ?? [];
      if (
        children.length !== 1 ||
        children[0].type !== "text" ||
        children[0].value !== node.url
      ) {
        return;
      }
      const chars = Array.from(node.url as string);
      const stop = chars.findIndex((char: string) =>
        CJK_STOP.test(char),
      );
      if (stop <= 0) {
        return;
      }
      const url = chars.slice(0, stop).join("");
      const suffix = chars.slice(stop).join("");
      node.url = url;
      node.children = [{ type: "text", value: url }];
      parent.children.splice(index + 1, 0, {
        type: "text",
        value: suffix,
      });
      },
    );
  };
}

export function rendererContentFromPre(node: unknown): string | undefined {
  const pre = node as HastNode | undefined;
  const code = pre?.children?.[0];
  if (!code || code.tagName !== "code") {
    return undefined;
  }
  const className = code.properties?.className;
  if (
    !Array.isArray(className) ||
    !className.includes("ling-renderer")
  ) {
    return undefined;
  }
  return textContent(code);
}

export function rendererCodeBlock(html: string): string {
  const content = html.replace(/\n$/, "");
  return `\`\`\`html type="renderer"\n${content}\n\`\`\``;
}

function visit(node: MarkdownNode): void {
  if (
    node.type === "code" &&
    node.lang === "html" &&
    typeof node.meta === "string" &&
    RENDERER_META.test(node.meta)
  ) {
    node.data ??= {};
    const properties = (node.data.hProperties ??= {});
    const className = Array.isArray(properties.className)
      ? properties.className
      : [];
    properties.className = [...className, "ling-renderer"];
  }

  for (const child of node.children ?? []) {
    visit(child);
  }
}

function textContent(node: HastNode): string {
  if (node.type === "text") {
    return node.value ?? "";
  }
  return (node.children ?? []).map(textContent).join("");
}
