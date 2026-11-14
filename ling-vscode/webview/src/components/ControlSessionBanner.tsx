import type { ControlSessionView } from "../types";

interface ControlSessionBannerProps {
  session: ControlSessionView;
  onStop(): void;
}

export function ControlSessionBanner({
  session,
  onStop,
}: ControlSessionBannerProps) {
  const label = session.app || session.windowTitle || "desktop";
  const detail =
    session.app && session.windowTitle && session.app !== session.windowTitle
      ? `${label} · ${session.windowTitle}`
      : label;
  return (
    <div className="control-session-banner" role="status">
      <span className="control-session-banner__dot" />
      <div className="control-session-banner__text">
        <strong>Ling 正在控制桌面</strong>
        <span>{detail}</span>
      </div>
      <button
        className="button control-session-banner__stop"
        type="button"
        onClick={onStop}
      >
        Stop
      </button>
    </div>
  );
}
