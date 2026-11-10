import type { UserAttachmentView } from "../types";
import { FileIcon } from "./icons";

interface MessageAttachmentsProps {
  attachments: UserAttachmentView[];
  onOpen(relativePath: string): void;
}

export function MessageAttachments({
  attachments,
  onOpen,
}: MessageAttachmentsProps) {
  if (attachments.length === 0) {
    return null;
  }

  const single = attachments.length === 1;
  return (
    <div
      className={`message-attachments${single ? " message-attachments--single" : ""}`}
      aria-label="Attached images"
    >
      {attachments.map((attachment) => {
        const isFile = attachment.kind === "file";
        return (
          <button
            className={`message-attachment${isFile ? " message-attachment--file" : ""}`}
            type="button"
            key={attachment.relativePath}
            aria-label={`Open ${attachment.name}`}
            title={attachment.name}
            onClick={() => onOpen(attachment.relativePath)}
          >
            {isFile ? (
              <span className="message-attachment-file">
                <FileIcon />
                <span className="message-attachment-file-name">
                  {attachment.name}
                </span>
                <span className="message-attachment-file-type">
                  {attachment.mediaType}
                </span>
              </span>
            ) : attachment.uri ? (
              <img
                className="message-attachment-image"
                src={attachment.uri}
                alt=""
                loading="lazy"
              />
            ) : (
              <span className="message-attachment-fallback">
                {attachment.name}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
