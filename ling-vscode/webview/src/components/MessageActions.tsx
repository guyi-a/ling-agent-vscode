import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, RegenerateIcon, TrashIcon } from "./icons";

interface MessageActionsProps {
  text: string;
  disabled: boolean;
  onCopy(text: string): void;
  onDelete?: () => void;
  onRegenerate?: () => void;
  showCopy?: boolean;
}

export function MessageActions({
  text,
  disabled,
  onCopy,
  onDelete,
  onRegenerate,
  showCopy = true,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(
    () => () => window.clearTimeout(timerRef.current),
    [],
  );

  useEffect(() => {
    if (disabled) {
      setConfirming(false);
    }
  }, [disabled]);

  function handleCopy() {
    onCopy(text);
    setCopied(true);
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      setCopied(false);
    }, 1400);
  }

  if (
    (!showCopy || !text) &&
    !onRegenerate &&
    !onDelete
  ) {
    return null;
  }

  if (confirming) {
    return (
      <div className="message-actions message-actions--confirm">
        <span className="message-action-confirm-label">
          Delete this message?
        </span>
        <button
          className="message-action-text"
          type="button"
          onClick={() => setConfirming(false)}
        >
          Cancel
        </button>
        <button
          className="message-action-text message-action-text--danger"
          type="button"
          onClick={() => {
            setConfirming(false);
            onDelete?.();
          }}
        >
          Delete
        </button>
      </div>
    );
  }

  return (
    <div className="message-actions">
      {showCopy && text ? (
        <button
          className="message-action"
          type="button"
          aria-label="Copy message"
          title={copied ? "Copied" : "Copy message"}
          onClick={handleCopy}
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
      ) : null}
      {onRegenerate ? (
        <button
          className="message-action"
          type="button"
          aria-label="Regenerate reply"
          title="Regenerate reply"
          disabled={disabled}
          onClick={onRegenerate}
        >
          <RegenerateIcon />
        </button>
      ) : null}
      {onDelete ? (
        <button
          className="message-action"
          type="button"
          aria-label="Delete message"
          title="Delete message"
          disabled={disabled}
          onClick={() => setConfirming(true)}
        >
          <TrashIcon />
        </button>
      ) : null}
    </div>
  );
}
