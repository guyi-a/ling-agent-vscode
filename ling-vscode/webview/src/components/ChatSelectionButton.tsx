import { useEffect, useState } from "react";
import type { UserSelectionRef } from "../types";

interface ChatSelectionButtonProps {
  onQuote(selection: UserSelectionRef): void;
  onCopy(text: string): void;
}

interface FloatingSelection {
  text: string;
  label: string;
  top: number;
  left: number;
}

export function ChatSelectionButton({
  onQuote,
  onCopy,
}: ChatSelectionButtonProps) {
  const [floating, setFloating] =
    useState<FloatingSelection | null>(null);

  useEffect(() => {
    function update(): void {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        setFloating(null);
        return;
      }

      const range = selection.getRangeAt(0);
      const node = range.commonAncestorContainer;
      const element =
        node.nodeType === Node.ELEMENT_NODE
          ? (node as Element)
          : node.parentElement;
      if (!element?.closest(".timeline")) {
        setFloating(null);
        return;
      }

      const text = selection.toString().trim();
      if (!text) {
        setFloating(null);
        return;
      }

      const rects = Array.from(range.getClientRects());
      const rect = rects.at(-1) ?? range.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) {
        setFloating(null);
        return;
      }

      const top =
        rect.bottom + 42 > window.innerHeight
          ? rect.top - 38
          : rect.bottom + 8;
      const left = Math.min(
        Math.max(8, rect.right - 92),
        Math.max(8, window.innerWidth - 128),
      );
      const label =
        element
          .closest("[data-selection-label]")
          ?.getAttribute("data-selection-label") ?? "Chat";

      setFloating({ text, label, top, left });
    }

    document.addEventListener("selectionchange", update);
    document.addEventListener("mouseup", update);
    document.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      document.removeEventListener("selectionchange", update);
      document.removeEventListener("mouseup", update);
      document.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, []);

  if (!floating) {
    return null;
  }

  return (
    <div
      className="chat-selection-actions"
      style={{ top: floating.top, left: floating.left }}
    >
      <button
        className="chat-selection-action"
        type="button"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => onCopy(floating.text)}
      >
        Copy
      </button>
      <button
        className="chat-selection-action chat-selection-action--primary"
        type="button"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          const quoted = limitSelectionText(floating.text);
          onQuote({
            kind: "chat",
            label: floating.label,
            text: quoted,
            lineCount: countLines(quoted),
          });
          window.getSelection()?.removeAllRanges();
          setFloating(null);
        }}
      >
        Quote
      </button>
    </div>
  );
}

const MAX_SELECTION_CHARS = 50_000;

function limitSelectionText(text: string): string {
  if (text.length <= MAX_SELECTION_CHARS) {
    return text;
  }
  return text.slice(0, MAX_SELECTION_CHARS) + "\n[truncated]";
}

function countLines(text: string): number {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  while (lines.length > 1 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return Math.max(1, lines.length);
}
