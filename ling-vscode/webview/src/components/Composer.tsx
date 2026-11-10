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
  UserAttachmentView,
  UserSelectionView,
} from "../types";
import { AttachmentChips } from "./AttachmentChips";
import { PlusIcon, SendIcon } from "./icons";
import { ModePicker } from "./ModePicker";
import { SelectionChips } from "./SelectionChips";

interface ComposerProps {
  mode: ApprovalMode;
  attachments: UserAttachmentView[];
  selections: UserSelectionView[];
  attachEnabled: boolean;
  onModeChange(value: ApprovalMode): void;
  onAttachFiles(files: File[]): void;
  onPickAttachments(): void;
  onRemoveAttachment(relativePath: string): void;
  onRemoveSelection(id: string): void;
  onSend(text: string, attachments: UserAttachmentView[]): void;
}

export function Composer({
  mode,
  attachments,
  selections,
  attachEnabled,
  onModeChange,
  onAttachFiles,
  onPickAttachments,
  onRemoveAttachment,
  onRemoveSelection,
  onSend,
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
      className="composer"
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
          <ModePicker value={mode} onChange={onModeChange} />
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
