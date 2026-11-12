import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { rendererCodeBlock } from "./markdownRenderer";

interface InlineRendererProps {
  html: string;
  streaming: boolean;
}

interface Theme {
  color: string;
  fontFamily: string;
  fontSize: string;
}

const BLOCKED_TAGS = new Set([
  "base",
  "embed",
  "form",
  "iframe",
  "link",
  "meta",
  "object",
  "script",
]);

export function InlineRenderer({
  html,
  streaming,
}: InlineRendererProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [theme, setTheme] = useState<Theme>({
    color: "#e9edf5",
    fontFamily: "inherit",
    fontSize: "14px",
  });
  const [rendered, setRendered] = useState(html);
  const [height, setHeight] = useState(60);
  const [copied, setCopied] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const copyTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!streaming) {
      setRendered(html);
      return;
    }
    const timer = window.setTimeout(() => setRendered(html), 70);
    return () => window.clearTimeout(timer);
  }, [html, streaming]);

  useEffect(() => {
    const updateTheme = () => {
      const element = wrapperRef.current;
      if (!element) {
        return;
      }
      const style = window.getComputedStyle(element);
      const next = {
        color: style.color,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
      };
      setTheme((current) =>
        current.color === next.color &&
        current.fontFamily === next.fontFamily &&
        current.fontSize === next.fontSize
          ? current
          : next,
      );
    };
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["class", "data-vscode-theme-kind"],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => window.clearTimeout(copyTimer.current),
    [],
  );

  const documentContent = useMemo(
    () => buildDocument(rendered, theme),
    [rendered, theme],
  );

  const measure = useCallback(() => {
    const document = iframeRef.current?.contentDocument;
    if (!document) {
      return;
    }
    const next = Math.max(
      document.documentElement?.scrollHeight ?? 0,
      document.body?.scrollHeight ?? 0,
    );
    setHeight(Math.min(Math.max(next, 40), 1400));
  }, []);

  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) {
      return;
    }
    let lastWidth = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== lastWidth) {
        lastWidth = element.clientWidth;
        measure();
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [measure]);

  async function copySource(): Promise<void> {
    const codeBlock = rendererCodeBlock(html);
    try {
      await navigator.clipboard.writeText(codeBlock);
    } catch {
      const area = document.createElement("textarea");
      area.value = codeBlock;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="inline-renderer" ref={wrapperRef}>
      {showSource ? (
        <pre className="inline-renderer-source">
          <code>{html}</code>
        </pre>
      ) : (
        <iframe
          ref={iframeRef}
          className="inline-renderer-frame"
          title="Inline visualization"
          sandbox="allow-same-origin"
          srcDoc={documentContent}
          style={{ height }}
          onLoad={() => {
            measure();
            window.setTimeout(measure, 50);
          }}
        />
      )}

      <div className="inline-renderer-toolbar">
        <button
          className="inline-renderer-action"
          type="button"
          onClick={() => void copySource()}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          className="inline-renderer-action"
          type="button"
          aria-pressed={showSource}
          onClick={() => setShowSource((current) => !current)}
        >
          {showSource ? "Hide source" : "Source"}
        </button>
      </div>
    </div>
  );
}

function buildDocument(html: string, theme: Theme): string {
  const sanitized = /<html[\s>]/i.test(html)
    ? sanitizeDocument(html)
    : sanitizeFragment(html);
  const meta =
    '<meta http-equiv="Content-Security-Policy" ' +
    'content="default-src \'none\'; style-src \'unsafe-inline\'; ' +
    'img-src data:; font-src data:;">';
  const style =
    "<style>" +
    "html,body{margin:0;padding:0;background:transparent;}" +
    "html{scrollbar-width:none;-ms-overflow-style:none;}" +
    "html::-webkit-scrollbar,body::-webkit-scrollbar{display:none;}" +
    `body{color:${safeCss(theme.color)};` +
    `font-family:${safeCss(theme.fontFamily)};` +
    `font-size:${safeCss(theme.fontSize)};line-height:1.5;` +
    "overflow:auto;}" +
    "svg{max-width:100%;height:auto;}" +
    "a{color:inherit;text-decoration:none;}" +
    "*{box-sizing:border-box;}" +
    "</style>";

  if (/<html[\s>]/i.test(sanitized)) {
    if (/<head[\s>]/i.test(sanitized)) {
      return sanitized.replace(
        /<head([^>]*)>/i,
        `<head$1>${meta}${style}`,
      );
    }
    return `<head>${meta}${style}</head>${sanitized}`;
  }

  return (
    "<!doctype html><html><head>" +
    meta +
    style +
    "</head><body>" +
    sanitized +
    "</body></html>"
  );
}

function sanitizeFragment(html: string): string {
  const template = document.createElement("template");
  template.innerHTML = html;
  sanitizeNode(template.content);
  return template.innerHTML;
}

function sanitizeDocument(html: string): string {
  const document = new DOMParser().parseFromString(html, "text/html");
  sanitizeNode(document.documentElement);
  return `<!doctype html>${document.documentElement.outerHTML}`;
}

function sanitizeNode(root: ParentNode): void {
  for (const element of Array.from(root.querySelectorAll("*"))) {
    const tag = element.tagName.toLowerCase();
    if (BLOCKED_TAGS.has(tag)) {
      element.remove();
      continue;
    }

    if (
      tag === "style" &&
      /@import|url\(\s*['"]?(?:https?:|\/\/)/i.test(
        element.textContent ?? "",
      )
    ) {
      element.remove();
      continue;
    }

    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on")) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (name === "srcset") {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (name === "src" || name === "href" || name === "xlink:href" || name === "poster") {
        const value = attribute.value.trim().toLowerCase();
        if (!value.startsWith("#") && !value.startsWith("data:")) {
          element.removeAttribute(attribute.name);
        }
        continue;
      }
      if (
        name === "style" &&
        /url\(\s*['"]?(?:https?:|\/\/)/i.test(attribute.value)
      ) {
        element.removeAttribute(attribute.name);
      }
    }
  }
}

function safeCss(value: string): string {
  return value.replace(/[<>{};]/g, "");
}
