import type {
  UserAttachmentRef,
  UserAttachmentView,
} from "./uiTypes";

export function toAttachmentRefs(
  attachments: readonly UserAttachmentView[],
): UserAttachmentRef[] {
  return attachments.map(({ relativePath, name, mediaType, kind }) => ({
    relativePath,
    name,
    mediaType,
    ...(kind ? { kind } : {}),
  }));
}
