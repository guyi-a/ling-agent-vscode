import type { EffortLevel } from "../types";

const EFFORTS: Array<{
  value: EffortLevel;
  label: string;
  title: string;
}> = [
  {
    value: "low",
    label: "Low",
    title: "Faster responses with less reasoning",
  },
  {
    value: "medium",
    label: "Medium",
    title: "Balanced reasoning effort",
  },
  {
    value: "high",
    label: "High",
    title: "More reasoning for harder tasks",
  },
  {
    value: "xhigh",
    label: "Extra high",
    title: "Even more reasoning for complex tasks",
  },
  {
    value: "max",
    label: "Max",
    title: "Maximum reasoning effort",
  },
];

const TRACK_WIDTH = 88;
const DOT_INSET = 6;
const DOT_STEP = (TRACK_WIDTH - DOT_INSET * 2) / (EFFORTS.length - 1);

interface EffortSliderProps {
  value: EffortLevel;
  onChange(value: EffortLevel): void;
}

export function EffortSlider({ value, onChange }: EffortSliderProps) {
  const index = Math.max(
    0,
    EFFORTS.findIndex((effort) => effort.value === value),
  );
  const knobLeft = DOT_INSET + index * DOT_STEP;

  function move(delta: number) {
    const next = Math.min(
      EFFORTS.length - 1,
      Math.max(0, index + delta),
    );
    if (next !== index) {
      onChange(EFFORTS[next].value);
    }
  }

  return (
    <div className="effort-slider">
      <span className="effort-slider__label">
        Effort <span>({EFFORTS[index].label})</span>
      </span>
      <div
        className="effort-control__track"
        role="radiogroup"
        aria-label="Reasoning effort"
        style={{ width: TRACK_WIDTH }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            move(-1);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            move(1);
          }
        }}
      >
        <span
          className="effort-control__fill"
          style={{
            left: DOT_INSET,
            width: knobLeft - DOT_INSET,
          }}
        />
        {EFFORTS.map((effort, effortIndex) => (
          <button
            key={effort.value}
            className="effort-control__dot"
            type="button"
            role="radio"
            aria-checked={effort.value === value}
            aria-label={`${effort.label} reasoning effort`}
            title={effort.title}
            style={{ left: DOT_INSET + effortIndex * DOT_STEP }}
            onClick={() => onChange(effort.value)}
          />
        ))}
        <span
          className="effort-control__knob"
          style={{ left: knobLeft }}
        />
      </div>
    </div>
  );
}
