import type { SVGProps } from "react";
import type { ToolAction } from "../types";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function LingMarkIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M5 4.75h14v10.5H9.25L5 19v-4.25V4.75Z" />
      <path d="M8.25 8.25h7.5M8.25 11.5h4.75" />
    </IconBase>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M12 5v14M5 12h14" />
    </IconBase>
  );
}

export function HistoryIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 9" />
      <path d="M4.5 4.5V9H9M12 8v4.5l3 1.75" />
    </IconBase>
  );
}

export function TasksIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M5 6.5h14M5 12h14M5 17.5h8" />
      <path d="M2.8 6.5h.01M2.8 12h.01M2.8 17.5h.01" />
    </IconBase>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="3.1" />
      <path d="M19.1 13.4a7.6 7.6 0 0 0 0-2.8l1.8-1.25-1.8-3.1-2.15.75a7.6 7.6 0 0 0-2.4-1.4L14.3 3.3h-4.6l-.25 2.3a7.6 7.6 0 0 0-2.4 1.4l-2.15-.75-1.8 3.1L4.9 10.6a7.6 7.6 0 0 0 0 2.8l-1.8 1.25 1.8 3.1 2.15-.75a7.6 7.6 0 0 0 2.4 1.4l.25 2.3h4.6l.25-2.3a7.6 7.6 0 0 0 2.4-1.4l2.15.75 1.8-3.1-1.8-1.25Z" />
    </IconBase>
  );
}

export function EyeIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M2.8 12s3.4-5.8 9.2-5.8S21.2 12 21.2 12s-3.4 5.8-9.2 5.8S2.8 12 2.8 12Z" />
      <circle cx="12" cy="12" r="2.6" />
    </IconBase>
  );
}

export function EyeOffIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M4 4 20 20" />
      <path d="M9.9 6.6A10.4 10.4 0 0 1 12 6.2c5.8 0 9.2 5.8 9.2 5.8a17 17 0 0 1-2.5 3.1" />
      <path d="M6.2 8.3A17.4 17.4 0 0 0 2.8 12S6.2 17.8 12 17.8c1.1 0 2.1-.2 3-.5" />
    </IconBase>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M19 12a7 7 0 1 1-2.05-4.95L19 9" />
      <path d="M19 4.5V9h-4.5" />
    </IconBase>
  );
}

export function ChevronIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m9 7 5 5-5 5" />
    </IconBase>
  );
}

export function FileIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M7 3.5h7l4 4v13H7v-17Z" />
      <path d="M14 3.5v4h4M10 12h5M10 15.5h5" />
    </IconBase>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M5 7h14M10 4h4l1 3H9l1-3Z" />
      <path d="M7 7l.8 12h8.4L17 7M10 11v5M14 11v5" />
    </IconBase>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </IconBase>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <rect x="9" y="9" width="10" height="10" rx="2" />
      <path d="M15 9V6.5A2.5 2.5 0 0 0 12.5 4h-6A2.5 2.5 0 0 0 4 6.5v6A2.5 2.5 0 0 0 6.5 15H9" />
    </IconBase>
  );
}

export function RegenerateIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M20 11a8 8 0 0 0-14.9-4M4 4.5V10h5.5" />
      <path d="M4 13a8 8 0 0 0 14.9 4M20 19.5V14h-5.5" />
    </IconBase>
  );
}

export function UndoIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m8 7-4 4 4 4" />
      <path d="M5 11h8a5 5 0 0 1 5 5v1" />
    </IconBase>
  );
}

export function SendIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m5 12 14-6-4.5 12-3-5.5L5 12Z" />
      <path d="m11.5 12.5 3-3" />
    </IconBase>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </IconBase>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="m6 12 4 4 8-8" />
    </IconBase>
  );
}

export function PlanIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M8.5 5H7v14h10V5h-1.5" />
      <path d="M9 3.5h6v3H9z" />
      <path d="M9.5 10h5M9.5 13h5M9.5 16h3" />
    </IconBase>
  );
}

export function ToolIcon({
  action,
  ...props
}: IconProps & { action: ToolAction }) {
  if (action === "Edit") {
    return (
      <IconBase {...props}>
        <path d="m5 19 3.5-.75L19 7.75 16.25 5 5.75 15.5 5 19Z" />
        <path d="m14.75 6.5 2.75 2.75" />
      </IconBase>
    );
  }

  if (action === "Run") {
    return (
      <IconBase {...props}>
        <path d="m7 5 11 7-11 7V5Z" />
      </IconBase>
    );
  }

  if (action === "Skill") {
    return (
      <IconBase {...props}>
        <path d="m12 4 1.8 4.2L18 10l-4.2 1.8L12 16l-1.8-4.2L6 10l4.2-1.8L12 4Z" />
        <path d="m18 15 .9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9L18 15Z" />
      </IconBase>
    );
  }

  if (action === "Plan") {
    return <PlanIcon {...props} />;
  }

  if (action === "Browser") {
    return (
      <IconBase {...props}>
        <circle cx="12" cy="12" r="8" />
        <path d="M4 12h16" />
        <path d="M12 4a12 12 0 0 1 0 16a12 12 0 0 1 0-16Z" />
      </IconBase>
    );
  }

  if (action === "Web") {
    return (
      <IconBase {...props}>
        <circle cx="10" cy="10" r="6" />
        <path d="M4 10h12" />
        <path d="M10 4a9 9 0 0 1 0 12a9 9 0 0 1 0-12Z" />
        <path d="m14.5 14.5 4 4" />
      </IconBase>
    );
  }

  if (action === "Memory") {
    return (
      <IconBase {...props}>
        <path d="M7 4h10v16l-5-3-5 3V4Z" />
        <path d="M10 9h4M10 12h4" />
      </IconBase>
    );
  }

  if (action === "MCP") {
    return (
      <IconBase {...props}>
        <path d="M8 7V4.5M16 7V4.5" />
        <path d="M6 7h12v4a6 6 0 0 1-12 0V7Z" />
        <path d="M12 17v3" />
      </IconBase>
    );
  }

  if (action === "Subagent") {
    return (
      <IconBase {...props}>
        <circle cx="6" cy="4.5" r="1.6" />
        <path d="M6 6.5v7a3 3 0 0 0 3 3h6" />
        <path d="m12.5 13.5 3 3-3 3" />
      </IconBase>
    );
  }

  return (
    <IconBase {...props}>
      <path d="M5 5.5h5.5a3 3 0 0 1 3 3v10a2.5 2.5 0 0 0-2.5-2.5H5v-10Z" />
      <path d="M19 5.5h-3.5a2 2 0 0 0-2 2v11a2.5 2.5 0 0 1 2.5-2.5H19v-10Z" />
    </IconBase>
  );
}
