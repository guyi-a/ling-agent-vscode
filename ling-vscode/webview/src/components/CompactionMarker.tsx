import type { CompactionItem } from "../types";

interface CompactionMarkerProps {
  item: CompactionItem;
}

export function CompactionMarker({ item }: CompactionMarkerProps) {
  return (
    <div className="compaction-marker" role="note">
      <span className="compaction-marker-line" aria-hidden="true" />
      <span className="compaction-marker-label">
        Context compacted · {item.replacedCount} messages folded
      </span>
      <span className="compaction-marker-line" aria-hidden="true" />
    </div>
  );
}
