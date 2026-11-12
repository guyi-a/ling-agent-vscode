import { LingMarkIcon } from "./icons";

export function BootScreen() {
  return (
    <div className="boot-screen" role="status" aria-live="polite">
      <span className="boot-screen__mark">
        <LingMarkIcon />
      </span>
      <p className="boot-screen__text">Loading conversation…</p>
    </div>
  );
}
