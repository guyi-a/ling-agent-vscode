import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  MAX_ATTACHMENT_IMAGE_BYTES,
  extensionForMime,
  isSupportedImageMime,
  mimeForFileName,
  resolveAttachmentFile,
  saveAttachmentImage,
} from "../src/attachments/store";

const PNG_BYTES = Uint8Array.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x01, 0x02,
]);

async function main(): Promise<void> {
  const dir = await mkdtemp(join(tmpdir(), "ling-attachments-"));
  try {
    assert.equal(isSupportedImageMime("image/png"), true);
    assert.equal(isSupportedImageMime("image/jpeg"), true);
    assert.equal(isSupportedImageMime("image/svg+xml"), false);
    assert.equal(extensionForMime("image/png"), ".png");
    assert.equal(extensionForMime("image/jpeg"), ".jpg");
    assert.equal(extensionForMime("image/svg+xml"), undefined);

    const saved = await saveAttachmentImage({
      workspaceRoot: dir,
      bytes: PNG_BYTES,
      mimeType: "image/png",
      suggestedName: "Screenshot 2026.png",
      id: "abcd1234",
    });
    assert.equal(
      saved.relativePath,
      ".agents/attachments/Screenshot-2026-abcd1234.png",
    );
    assert.equal(saved.name, "Screenshot-2026-abcd1234.png");
    assert.equal(saved.mediaType, "image/png");

    const absolute = resolveAttachmentFile(dir, saved.relativePath);
    assert.deepEqual(
      new Uint8Array(await readFile(absolute)),
      PNG_BYTES,
    );
    assert.equal(
      resolveAttachmentFile(
        dir,
        ".ling/attachments/Screenshot-2026-abcd1234.png",
      ),
      resolveAttachmentFile(
        dir,
        ".agents/attachments/Screenshot-2026-abcd1234.png",
      ),
    );

    const pasted = await saveAttachmentImage({
      workspaceRoot: dir,
      bytes: PNG_BYTES,
      mimeType: "image/png",
      id: "efgh5678",
    });
    assert.equal(
      pasted.relativePath,
      ".agents/attachments/pasted-efgh5678.png",
    );

    assert.equal(mimeForFileName("doc.pdf"), "application/pdf");
    assert.equal(mimeForFileName("image.png"), "image/png");
    assert.equal(
      mimeForFileName("unknown.xyz"),
      "application/octet-stream",
    );

    await assert.rejects(
      saveAttachmentImage({
        workspaceRoot: dir,
        bytes: PNG_BYTES,
        mimeType: "text/plain",
      }),
    );
    await assert.rejects(
      saveAttachmentImage({
        workspaceRoot: dir,
        bytes: new Uint8Array(),
        mimeType: "image/png",
      }),
    );
    await assert.rejects(
      saveAttachmentImage({
        workspaceRoot: dir,
        bytes: new Uint8Array(MAX_ATTACHMENT_IMAGE_BYTES + 1),
        mimeType: "image/png",
      }),
    );

    assert.throws(() =>
      resolveAttachmentFile(dir, "../escape.png"),
    );
    assert.throws(() =>
      resolveAttachmentFile(dir, "C:/escape.png"),
    );
    assert.throws(() =>
      resolveAttachmentFile(dir, ".agents\\attachments\\a.png"),
    );
    assert.throws(() =>
      resolveAttachmentFile(dir, ".agents/attachments/../../a.png"),
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }

  console.log("Ling attachment store tests passed");
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
