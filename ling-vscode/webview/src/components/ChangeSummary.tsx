import type { WorkspaceChangeSummary } from "../types";
import { EyeIcon, ToolIcon, UndoIcon } from "./icons";

interface ChangeSummaryProps {
  changes: WorkspaceChangeSummary[];
  reverted: boolean;
  viewDisabled: boolean;
  revertDisabled: boolean;
  onView(): void;
  onRevert(): void;
}

export function ChangeSummary({
  changes,
  reverted,
  viewDisabled,
  revertDisabled,
  onView,
  onRevert,
}: ChangeSummaryProps) {
  const added = changes.reduce(
    (total, change) => total + change.added,
    0,
  );
  const removed = changes.reduce(
    (total, change) => total + change.removed,
    0,
  );

  return (
    <section className="change-summary" aria-label="File changes">
      <header className="change-summary__header">
        <span className="change-summary__icon">
          <ToolIcon action="Edit" />
        </span>
        <div className="change-summary__title">
          <strong>
            {reverted ? "已撤销修改" : `已编辑 ${changes.length} 个文件`}
          </strong>
          <span className="change-summary__stats">
            <span className="change-summary__added">+{added}</span>
            <span className="change-summary__removed">−{removed}</span>
          </span>
        </div>
        <div className="change-summary__actions">
          <button
            type="button"
            className="change-summary__button"
            disabled={viewDisabled}
            onClick={onView}
          >
            <EyeIcon />
            查看变更
          </button>
          <button
            type="button"
            className="change-summary__button"
            disabled={revertDisabled}
            onClick={onRevert}
          >
            <UndoIcon />
            {reverted ? "已撤销" : "撤销"}
          </button>
        </div>
      </header>
      <ul className="change-summary__files">
        {changes.map((change) => (
          <li className="change-summary__file" key={change.path}>
            <span className="change-summary__path" title={change.path}>
              {change.path}
            </span>
            <span className="change-summary__file-stats">
              <span className="change-summary__added">
                +{change.added}
              </span>
              <span className="change-summary__removed">
                −{change.removed}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
