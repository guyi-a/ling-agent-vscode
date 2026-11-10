import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import type { ApprovalMode } from "../types";

const MODES: Array<{ value: ApprovalMode; label: string; title: string }> = [
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
  onChange(value: ApprovalMode): void;
}

export function ModePicker({ value, onChange }: ModePickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
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

  function handleMenuKeyDown(event: KeyboardEvent<HTMLUListElement>) {
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
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${baseId}-menu` : undefined}
        aria-label={`Approval mode: ${current.label}`}
        title={current.title}
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
        <ul
          ref={menuRef}
          id={`${baseId}-menu`}
          className="mode-picker__menu"
          role="listbox"
          aria-label="Approval mode"
          aria-activedescendant={`${baseId}-option-${activeIndex}`}
          tabIndex={-1}
          onKeyDown={handleMenuKeyDown}
        >
          {MODES.map((mode, index) => (
            <li
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
              <span className="mode-picker__label">{mode.label}</span>
              <span className="mode-picker__hint">{mode.title}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
