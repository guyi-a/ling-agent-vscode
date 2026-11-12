import {
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import type {
  ApprovalMode,
  EffortLevel,
  UserAttachmentView,
  UserSelectionView,
} from "../types";
import { AttachmentChips } from "./AttachmentChips";
import { PlusIcon, SendIcon, StopIcon } from "./icons";
import { ModePicker } from "./ModePicker";
import { SelectionChips } from "./SelectionChips";

interface ComposerProps {
  mode: ApprovalMode;
  effort: EffortLevel;
  attachments: UserAttachmentView[];
  selections: UserSelectionView[];
  attachEnabled: boolean;
  busy: boolean;
  onModeChange(value: ApprovalMode): void;
  onEffortChange(value: EffortLevel): void;
  onAttachFiles(files: File[]): void;
  onPickAttachments(): void;
  onRemoveAttachment(relativePath: string): void;
  onRemoveSelection(id: string): void;
  onSend(text: string, attachments: UserAttachmentView[]): void;
  onStop(): void;
}

export function Composer({
  mode,
  effort,
  attachments,
  selections,
  attachEnabled,
  busy,
  onModeChange,
  onEffortChange,
  onAttachFiles,
  onPickAttachments,
  onRemoveAttachment,
  onRemoveSelection,
  onSend,
  onStop,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    const maxHeight = 132;
    textarea.style.height = "auto";
    const next = Math.min(
      Math.max(textarea.scrollHeight, 33),
      maxHeight,
    );
    textarea.style.height = `${next}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [value]);

  function submit() {
    const text = value.trim();
    if (!text && attachments.length === 0 && selections.length === 0) {
      return;
    }

    onSend(text, attachments);
    setValue("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    if (!attachEnabled) {
      return;
    }
    const images = clipboardImages(event.clipboardData);
    if (images.length === 0) {
      return;
    }
    event.preventDefault();
    onAttachFiles(images);
  }

  function handleDragOver(event: DragEvent<HTMLFormElement>) {
    if (!attachEnabled) {
      return;
    }
    if (
      !Array.from(event.dataTransfer?.types ?? []).includes("Files")
    ) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleDrop(event: DragEvent<HTMLFormElement>) {
    if (!attachEnabled) {
      return;
    }
    const images = droppedImages(event.dataTransfer);
    if (images.length === 0) {
      return;
    }
    event.preventDefault();
    onAttachFiles(images);
  }

  return (
    <form
      className={`composer composer--${mode}`}
      onSubmit={handleSubmit}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <SelectionChips
        selections={selections}
        onRemove={onRemoveSelection}
      />
      <AttachmentChips
        attachments={attachments}
        onRemove={onRemoveAttachment}
      />
      <textarea
        ref={textareaRef}
        value={value}
        rows={1}
        placeholder="Ask, edit, run, or paste an image..."
        aria-label="Message Ling"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
      <div className="composer-row">
        <button
          className="composer-add"
          type="button"
          aria-label="Attach images"
          title="Attach images"
          disabled={!attachEnabled}
          onClick={onPickAttachments}
        >
          <PlusIcon />
        </button>
        <div className="composer-actions">
          <ModePicker
            value={mode}
            effort={effort}
            onChange={onModeChange}
            onEffortChange={onEffortChange}
          />
          {busy ? (
            <button
              className="send-button send-button--stop"
              type="button"
              aria-label="Stop run"
              title="Stop run"
              onClick={onStop}
            >
              <StopIcon />
            </button>
          ) : (
            <button
              className="send-button"
              type="submit"
              aria-label="Send message"
              title="Send message"
              disabled={
                !value.trim() &&
                attachments.length === 0 &&
                selections.length === 0
              }
            >
              <SendIcon />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

function clipboardImages(data: DataTransfer | null): File[] {
  const files: File[] = [];
  for (const item of data?.items ?? []) {
    if (item.kind !== "file" || !item.type.startsWith("image/")) {
      continue;
    }
    const file = item.getAsFile();
    if (file) {
      files.push(file);
    }
  }
  return files;
}

function droppedImages(data: DataTransfer | null): File[] {
  return Array.from(data?.files ?? []).filter((file) =>
    file.type.startsWith("image/"),
  );
}
