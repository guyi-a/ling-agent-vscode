import { useEffect, useRef, type Dispatch } from "react";
import type {
  ApprovalMode,
  BackgroundTaskView,
  HistorySession,
  IndexBuildStats,
  IndexStatus,
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
  onIndexResult: (
    ok: boolean,
    stats?: IndexBuildStats,
    error?: string,
  ) => void,
  onIndexStatus: (status: IndexStatus) => void,
  onBackgroundResult: (
    callId: string,
    accepted: boolean,
    task?: BackgroundTaskView,
    error?: string,
  ) => void,
  onReady: () => void,
): void {
  // Keep handlers reachable from one stable subscription. Re-subscribing on
  // every render can drop events that arrive between cleanup and setup.
  const handlers = useRef({
    onHistoryList,
    onWorkspaceName,
    onApprovalMode,
    onSettings,
    onSettingsSaved,
    onAttachmentSaved,
    onSelectionAdded,
    onIndexResult,
    onIndexStatus,
    onBackgroundResult,
    onReady,
  });
  handlers.current = {
    onHistoryList,
    onWorkspaceName,
    onApprovalMode,
    onSettings,
    onSettingsSaved,
    onAttachmentSaved,
    onSelectionAdded,
    onIndexResult,
    onIndexStatus,
    onBackgroundResult,
    onReady,
  };

  useEffect(
    () =>
      host.subscribe((message) => {
        const current = handlers.current;
        if (message.type === "ling.ui-event") {
          if (
            message.event.type === "session.snapshot" &&
            message.event.snapshot.approvalMode
          ) {
            current.onApprovalMode(
              message.event.snapshot.approvalMode,
            );
          }
          dispatch(message.event);
          return;
        }
        if (message.type === "ling.history-list") {
          current.onHistoryList(message.sessions, message.error);
          return;
        }
        if (message.type === "ling.settings") {
          current.onSettings(message.settings, message.error);
          return;
        }
        if (message.type === "ling.settings-saved") {
          current.onSettingsSaved(
            message.settings,
            message.error,
            message.warning,
          );
          return;
        }
        if (message.type === "ling.attachment-saved") {
          current.onAttachmentSaved(
            message.requestId,
            message.attachment,
            message.error,
          );
          return;
        }
        if (message.type === "ling.selection-added") {
          current.onSelectionAdded(
            message.selection,
            message.error,
          );
          return;
        }
        if (message.type === "ling.index-result") {
          current.onIndexResult(
            message.ok,
            message.stats,
            message.error,
          );
          return;
        }
        if (message.type === "ling.index-status") {
          current.onIndexStatus(message.status);
          return;
        }
        if (message.type === "ling.mode-changed") {
          current.onApprovalMode(message.mode);
          return;
        }
        if (message.type === "ling.background-result") {
          current.onBackgroundResult(
            message.callId,
            message.accepted,
            message.task,
            message.error,
          );
          return;
        }
        if (message.type === "ling.ready") {
          current.onReady();
          return;
        }
        current.onWorkspaceName(message.workspaceName);
      }),
    [dispatch, host],
  );
}
