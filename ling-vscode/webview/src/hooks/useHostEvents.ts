import { useEffect, type Dispatch } from "react";
import type {
  ApprovalMode,
  HistorySession,
  SettingsView,
  UserAttachmentView,
  UserSelectionView,
} from "../../../shared/uiTypes";
import type { HostBridge } from "../host";
import type { UiAction } from "../protocol";

export function useHostEvents(
  host: HostBridge,
  dispatch: Dispatch<UiAction>,
  onHistoryList: (
    sessions: HistorySession[],
    error?: string,
  ) => void,
  onWorkspaceName: (name: string) => void,
  onApprovalMode: (mode: ApprovalMode) => void,
  onSettings: (settings: SettingsView, error?: string) => void,
  onSettingsSaved: (
    settings: SettingsView | undefined,
    error?: string,
    warning?: string,
  ) => void,
  onAttachmentSaved: (
    requestId: string,
    attachment?: UserAttachmentView,
    error?: string,
  ) => void,
  onSelectionAdded: (
    selection?: UserSelectionView,
    error?: string,
  ) => void,
): void {
  useEffect(
    () =>
      host.subscribe((message) => {
        if (message.type === "ling.ui-event") {
          if (
            message.event.type === "session.snapshot" &&
            message.event.snapshot.approvalMode
          ) {
            onApprovalMode(message.event.snapshot.approvalMode);
          }
          dispatch(message.event);
          return;
        }
        if (message.type === "ling.history-list") {
          onHistoryList(message.sessions, message.error);
          return;
        }
        if (message.type === "ling.settings") {
          onSettings(message.settings, message.error);
          return;
        }
        if (message.type === "ling.settings-saved") {
          onSettingsSaved(
            message.settings,
            message.error,
            message.warning,
          );
          return;
        }
        if (message.type === "ling.attachment-saved") {
          onAttachmentSaved(
            message.requestId,
            message.attachment,
            message.error,
          );
          return;
        }
        if (message.type === "ling.selection-added") {
          onSelectionAdded(message.selection, message.error);
          return;
        }
        onWorkspaceName(message.workspaceName);
      }),
    [
      dispatch,
      host,
      onApprovalMode,
      onHistoryList,
      onSettings,
      onSettingsSaved,
      onAttachmentSaved,
      onSelectionAdded,
      onWorkspaceName,
    ],
  );
}
