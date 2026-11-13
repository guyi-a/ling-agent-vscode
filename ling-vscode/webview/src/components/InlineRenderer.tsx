import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
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

const MIN_FRAME_HEIGHT = 40;
const MAX_FRAME_HEIGHT = 1400;
const MAX_EXPANDED_HEIGHT = 4000;

export function InlineRenderer({
  html,
  streaming,
}: InlineRendererProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [theme, setTheme] = useState<Theme>({
    color: "#e9edf5",
    fontFamily: "inherit",
    fontSize: "14px",
  });
  const [rendered, setRendered] = useState(html);
  const [copied, setCopied] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [expanded, setExpanded] = useState(false);
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

  useEffect(() => {
    if (!expanded) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setExpanded(false);
      }
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [expanded]);

  const documentContent = useMemo(
    () => buildDocument(rendered, theme),
    [rendered, theme],
  );

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
        <RendererFrame
          document={documentContent}
          className="inline-renderer-frame"
        />
      )}

      <div className="inline-renderer-toolbar">
        {showSource ? null : (
          <button
            className="inline-renderer-action"
            type="button"
            aria-haspopup="dialog"
            title="Expand diagram"
            onClick={() => setExpanded(true)}
          >
            Expand
          </button>
        )}
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

      {expanded
        ? createPortal(
            <div
              className="inline-renderer-overlay"
              role="dialog"
              aria-modal="true"
              aria-label="Expanded diagram"
              onClick={(event) => {
                if (event.target === event.currentTarget) {
                  setExpanded(false);
                }
              }}
            >
              <div className="inline-renderer-expanded">
                <div className="inline-renderer-expanded-bar">
                  <span className="inline-renderer-expanded-label">
                    Diagram
                  </span>
                  <button
                    ref={closeButtonRef}
                    className="inline-renderer-action"
                    type="button"
                    onClick={() => setExpanded(false)}
                  >
                    Close
                  </button>
                </div>
                <div className="inline-renderer-expanded-stage">
                  <RendererFrame
                    document={documentContent}
                    className="inline-renderer-expanded-frame"
                    maxHeight={MAX_EXPANDED_HEIGHT}
                  />
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

interface RendererFrameProps {
  document: string;
  className: string;
  maxHeight?: number;
}

/**
 * Render sandboxed markup and keep the iframe exactly as tall as the content.
 *
 * The frame is re-measured after load, after the srcDoc changes and whenever
 * its width changes, because an SVG's rendered height depends on the width
 * the drawing is laid out at.
 */
function RendererFrame({
  document: documentContent,
  className,
  maxHeight = MAX_FRAME_HEIGHT,
}: RendererFrameProps) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const timers = useRef<number[]>([]);
  const [height, setHeight] = useState(MIN_FRAME_HEIGHT);

  const measure = useCallback(() => {
    const next = frameContentHeight(frameRef.current);
    if (next <= 0) {
      return;
    }
    setHeight(Math.min(Math.max(next, MIN_FRAME_HEIGHT), maxHeight));
  }, [maxHeight]);

  const measureSoon = useCallback(() => {
    for (const timer of timers.current) {
      window.clearTimeout(timer);
    }
    timers.current = [
      window.setTimeout(measure, 0),
      window.setTimeout(measure, 80),
      window.setTimeout(measure, 260),
    ];
  }, [measure]);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) {
      return;
    }
    let lastWidth = frame.clientWidth;
    const observer = new ResizeObserver(() => {
      if (frame.clientWidth === lastWidth) {
        return;
      }
      lastWidth = frame.clientWidth;
      measure();
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, [measure]);

  useEffect(
    () => () => {
      for (const timer of timers.current) {
        window.clearTimeout(timer);
      }
    },
    [],
  );

  useEffect(measureSoon, [documentContent, measureSoon]);

  return (
    <iframe
      ref={frameRef}
      className={className}
      title="Inline visualization"
      sandbox="allow-same-origin"
      srcDoc={documentContent}
      style={{ height }}
      onLoad={measureSoon}
    />
  );
}

function frameContentHeight(frame: HTMLIFrameElement | null): number {
  const contentDocument = frame?.contentDocument;
  const body = contentDocument?.body;
  if (!contentDocument || !body) {
    return 0;
  }
  // `scrollHeight` on <html> and <body> never drops below the iframe viewport,
  // so measuring it makes the frame grow-only and leaves dead space under the
  // drawing. Measure the body box and its children instead.
  let height = body.getBoundingClientRect().height;
  for (const child of Array.from(body.children)) {
    const rect = child.getBoundingClientRect();
    if (rect.bottom > height) {
      height = rect.bottom;
    }
  }
  return Math.ceil(height);
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
    "body>svg{display:block;margin:0 auto;}" +
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
