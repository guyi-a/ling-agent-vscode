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
