"""User attachment metadata shared by session, timeline, and RPC."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

AttachmentKind = Literal["image", "file"]

MAX_USER_ATTACHMENTS = 4
MAX_ATTACHMENT_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ATTACHMENT_FILE_BYTES = 25 * 1024 * 1024

IMAGE_UNSUPPORTED_MESSAGE = (
    "The selected model does not support image input. "
    "Choose a vision-capable model in Settings, "
    "or remove the image and try again."
)

_IMAGE_ERROR_MARKERS = (
    "image_url",
    "image input",
    "image content",
    "does not support image",
    "doesn't support image",
    "not support image",
    "unsupported image",
    "vision",
    "multimodal",
    "invalid content type",
)


def friendly_provider_error(message: str | None) -> str | None:
    """Replace raw vision-provider failures with an actionable message."""
    if not message:
        return message
    lowered = message.lower()
    if any(marker in lowered for marker in _IMAGE_ERROR_MARKERS):
        return IMAGE_UNSUPPORTED_MESSAGE
    return message


@dataclass(slots=True, frozen=True)
class UserAttachment:
    """An attachment owned by one user message.

    Images use a workspace-relative copy. File attachments may reference the
    user's original absolute path, because they are read on demand by tools.
    """

    relative_path: str
    name: str
    media_type: str
    kind: AttachmentKind = "image"

    def __post_init__(self) -> None:
        if self.kind not in ("image", "file"):
            raise ValueError("attachment kind must be 'image' or 'file'")
        if not self.relative_path:
            raise ValueError("attachment relative_path must not be empty")
        if not self.name:
            raise ValueError("attachment name must not be empty")
        if not self.media_type:
            raise ValueError("attachment media_type must not be empty")
        if self.kind == "image" and not self.media_type.startswith("image/"):
            raise ValueError("image attachment media_type must be an image type")

        # Images and workspace files stay relative; user-selected files may
        # reference their original absolute path on disk.
        is_absolute = (
            Path(self.relative_path).is_absolute()
            or PureWindowsPath(self.relative_path).is_absolute()
        )
        if self.kind == "file" and is_absolute:
            return

        if "\\" in self.relative_path:
            raise ValueError("attachment path must use '/' separators")
        if len(self.relative_path) >= 2 and self.relative_path[1] == ":":
            raise ValueError("attachment path must be relative")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("attachment path must stay inside the workspace")


__all__ = [
    "AttachmentKind",
    "IMAGE_UNSUPPORTED_MESSAGE",
    "MAX_ATTACHMENT_FILE_BYTES",
    "MAX_ATTACHMENT_IMAGE_BYTES",
    "MAX_USER_ATTACHMENTS",
    "UserAttachment",
    "friendly_provider_error",
]
