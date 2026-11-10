import type { UserAttachmentView } from "../types";
import { CloseIcon, FileIcon } from "./icons";

interface AttachmentChipsProps {
  attachments: UserAttachmentView[];
  onRemove(relativePath: string): void;
}

export function AttachmentChips({
  attachments,
  onRemove,
}: AttachmentChipsProps) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className="attachment-chips" aria-label="Attached images">
      {attachments.map((attachment) => {
        const isFile = attachment.kind === "file";
        return (
        <div
          className={`attachment-chip${isFile ? " attachment-chip--file" : ""}`}
          key={attachment.relativePath}
          title={attachment.name}
        >
          {isFile ? (
            <span className="attachment-chip-file">
              <FileIcon />
              <span className="attachment-chip-file-name">
                {attachment.name}
              </span>
            </span>
          ) : attachment.uri ? (
            <img
              className="attachment-chip-thumb"
              src={attachment.uri}
              alt=""
            />
          ) : (
            <span className="attachment-chip-fallback">
              {attachment.name}
            </span>
          )}
          <button
            className="attachment-chip-remove"
            type="button"
            aria-label={`Remove ${attachment.name}`}
            title={`Remove ${attachment.name}`}
            onClick={() => onRemove(attachment.relativePath)}
          >
            <CloseIcon />
          </button>
        </div>
        );
      })}
    </div>
  );
}
