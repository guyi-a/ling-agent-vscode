import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type { ApprovalMode, EffortLevel } from "../types";
import { EffortSlider } from "./EffortSlider";
import { CheckIcon } from "./icons";

const MODES: Array<{
  value: ApprovalMode;
  label: string;
  title: string;
}> = [
  {
    value: "manual",
    label: "Manual",
    title: "Ask before writes and normal commands",
  },
  {
    value: "accept-write",
    label: "Accept writes",
    title: "Allow workspace writes, ask before normal commands",
  },
  {
    value: "auto",
    label: "Auto",
    title: "Allow normal operations, ask before risky operations",
  },
];

interface ModePickerProps {
  value: ApprovalMode;
  effort: EffortLevel;
  onChange(value: ApprovalMode): void;
  onEffortChange(value: EffortLevel): void;
}

export function ModePicker({
  value,
  effort,
  onChange,
  onEffortChange,
}: ModePickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const current = MODES.find((mode) => mode.value === value) ?? MODES[0];

  useEffect(() => {
    if (!open) {
      return;
    }

    menuRef.current?.focus();

    function handlePointerDown(event: PointerEvent) {
      if (rootRef.current?.contains(event.target as Node)) {
        return;
      }
      setOpen(false);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () =>
      document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function openMenu() {
    const index = MODES.findIndex((mode) => mode.value === value);
    setActiveIndex(index < 0 ? 0 : index);
    setOpen(true);
  }

  function select(next: ApprovalMode) {
    onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function handleMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex((index) => (index + 1) % MODES.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex(
          (index) => (index - 1 + MODES.length) % MODES.length,
        );
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(MODES.length - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        select(MODES[activeIndex].value);
        break;
      case "Escape":
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
        break;
      case "Tab":
        setOpen(false);
        break;
      default:
        break;
    }
  }

  return (
    <div className="mode-picker" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="mode-picker__trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? `${baseId}-menu` : undefined}
        aria-label={`Chat options: ${current.label}`}
        title="Approval mode and reasoning effort"
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            openMenu();
          }
        }}
      >
        <span>{current.label}</span>
        <svg viewBox="0 0 12 12" aria-hidden="true">
          <path
            d="m3 4.5 3 3 3-3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open ? (
        <div
          className="mode-picker__menu"
          id={`${baseId}-menu`}
          role="dialog"
          aria-label="Chat options"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              setOpen(false);
              triggerRef.current?.focus();
            }
          }}
        >
          <p className="mode-picker__title">Modes</p>
          <div
            ref={menuRef}
            className="mode-picker__options"
            role="listbox"
            aria-label="Approval mode"
            aria-activedescendant={`${baseId}-option-${activeIndex}`}
            tabIndex={-1}
            onKeyDown={handleMenuKeyDown}
          >
            {MODES.map((mode, index) => (
              <div
                key={mode.value}
                id={`${baseId}-option-${index}`}
                className={
                  index === activeIndex
                    ? "mode-picker__option is-active"
                    : "mode-picker__option"
                }
                role="option"
                aria-selected={mode.value === value}
                onMouseEnter={() => setActiveIndex(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(mode.value)}
              >
                <div className="mode-picker__option-text">
                  <span className="mode-picker__label">
                    {mode.label}
                  </span>
                  <span className="mode-picker__hint">
                    {mode.title}
                  </span>
                </div>
                {mode.value === value ? (
                  <CheckIcon className="mode-picker__check" />
                ) : null}
              </div>
            ))}
          </div>

          <div className="mode-picker__divider" />

          <EffortSlider value={effort} onChange={onEffortChange} />
          <p className="mode-picker__effort-hint">
            Higher effort spends more reasoning tokens.
          </p>
        </div>
      ) : null}
    </div>
  );
}
