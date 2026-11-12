import { useMemo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { InlineRenderer } from "./InlineRenderer";
import {
  remarkCjkAutolink,
  remarkRendererFence,
  rendererContentFromPre,
} from "./markdownRenderer";

interface MarkdownMessageProps {
  content: string;
  streaming: boolean;
}

const baseComponents: Components = {
  a({ children, ...props }) {
    return (
      <a {...props} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
  table({ children }) {
    return (
      <div className="markdown-table-wrap">
        <table>{children}</table>
      </div>
    );
  },
};

export function MarkdownMessage({
  content,
  streaming,
}: MarkdownMessageProps) {
  const components = useMemo<Components>(
    () => ({
      ...baseComponents,
      pre({ node, children }) {
        const renderer = rendererContentFromPre(node);
        if (renderer !== undefined) {
          return (
            <InlineRenderer html={renderer} streaming={streaming} />
          );
        }
        return <pre>{children}</pre>;
      },
    }),
    [streaming],
  );

  return (
    <div
      className={`markdown-body${streaming ? " markdown-body--streaming" : ""}`}
    >
      <Markdown
        remarkPlugins={[
          remarkGfm,
          remarkCjkAutolink,
          remarkRendererFence,
        ]}
        components={components}
      >
        {content}
      </Markdown>
      {streaming ? (
        <span className="generating-status" role="status">
          Generating
        </span>
      ) : null}
    </div>
  );
}
