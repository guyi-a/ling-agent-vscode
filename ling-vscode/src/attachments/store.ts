import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export const MAX_ATTACHMENT_IMAGE_BYTES = 4 * 1024 * 1024;
export const MAX_ATTACHMENT_FILE_BYTES = 25 * 1024 * 1024;

const ATTACHMENTS_SUBDIR = path.join(".agents", "attachments");

const IMAGE_MIME_EXTENSIONS: Record<string, string> = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/bmp": ".bmp",
};

const FILE_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".bmp": "image/bmp",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".json": "application/json",
  ".csv": "text/csv",
  ".log": "text/plain",
  ".py": "text/x-python",
  ".ts": "text/typescript",
  ".tsx": "text/typescript",
  ".js": "text/javascript",
  ".jsx": "text/javascript",
  ".html": "text/html",
  ".css": "text/css",
  ".xml": "application/xml",
  ".yaml": "application/yaml",
  ".yml": "application/yaml",
  ".toml": "application/toml",
  ".sh": "text/x-shellscript",
  ".ps1": "text/x-powershell",
  ".doc": "application/msword",
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xls": "application/vnd.ms-excel",
  ".xlsx":
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".ppt": "application/vnd.ms-powerpoint",
  ".pptx":
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

export interface SavedAttachmentImage {
  relativePath: string;
  name: string;
  mediaType: string;
}

export function isSupportedImageMime(mimeType: string): boolean {
  return Object.prototype.hasOwnProperty.call(
    IMAGE_MIME_EXTENSIONS,
    mimeType,
  );
}

export function extensionForMime(mimeType: string): string | undefined {
  return IMAGE_MIME_EXTENSIONS[mimeType];
}

export function mimeForFileName(fileName: string): string {
  return (
    FILE_MIME_TYPES[path.extname(fileName).toLowerCase()] ??
    "application/octet-stream"
  );
}

export function assertSafeRelativePath(relativePath: string): void {
  if (
    !relativePath ||
    relativePath.includes("\\") ||
    path.isAbsolute(relativePath) ||
    relativePath.split("/").includes("..")
  ) {
    throw new Error("Attachment path must stay inside the workspace");
  }
}

export function resolveAttachmentFile(
  workspaceRoot: string,
  relativePath: string,
): string {
  const normalizedPath = relativePath.startsWith(".ling/attachments/")
    ? `.agents/attachments/${relativePath.slice(
        ".ling/attachments/".length,
      )}`
    : relativePath;
  assertSafeRelativePath(normalizedPath);
  const root = path.resolve(workspaceRoot);
  const resolved = path.resolve(root, ...normalizedPath.split("/"));
  const relative = path.relative(root, resolved);
  if (
    !relative ||
    relative.startsWith("..") ||
    path.isAbsolute(relative)
  ) {
    throw new Error("Attachment path must stay inside the workspace");
  }
  return resolved;
}

export async function saveAttachmentImage(options: {
  workspaceRoot: string;
  bytes: Uint8Array;
  mimeType: string;
  suggestedName?: string;
  id?: string;
}): Promise<SavedAttachmentImage> {
  const extension = extensionForMime(options.mimeType);
  if (!extension) {
    throw new Error(`Unsupported image type: ${options.mimeType}`);
  }
  if (options.bytes.length < 1) {
    throw new Error("Image is empty");
  }
  if (options.bytes.length > MAX_ATTACHMENT_IMAGE_BYTES) {
    throw new Error(
      `Image exceeds ${MAX_ATTACHMENT_IMAGE_BYTES} bytes`,
    );
  }

  const directory = path.join(
    path.resolve(options.workspaceRoot),
    ATTACHMENTS_SUBDIR,
  );
  await mkdir(directory, { recursive: true });

  const stem = safeStem(options.suggestedName, "pasted");
  const suffix = (options.id ?? randomUUID()).slice(0, 8);
  const name = `${stem}-${suffix}${extension}`;
  const absolute = path.join(directory, name);
  await writeFile(absolute, options.bytes);

  return {
    relativePath: `${ATTACHMENTS_SUBDIR.split(path.sep).join("/")}/${name}`,
    name,
    mediaType: options.mimeType,
  };
}

function safeStem(
  suggestedName: string | undefined,
  fallback = "file",
): string {
  const base = (suggestedName ?? "").trim();
  const withoutExtension = base.replace(/\.[^.]*$/, "");
  const sanitized = withoutExtension
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return sanitized || fallback;
}
