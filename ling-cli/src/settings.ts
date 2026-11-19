export type ConfigFieldKind =
  | "string"
  | "secret"
  | "number"
  | "boolean"
  | "enum";

export interface ConfigField {
  key: string;
  label: string;
  kind: ConfigFieldKind;
  defaultValue: string;
  options?: string[];
  min?: number;
  max?: number;
  integer?: boolean;
  required?: boolean;
}

export const CONFIG_FIELDS: ConfigField[] = [
  {
    key: "apiKey",
    label: "apiKey",
    kind: "secret",
    defaultValue: "",
  },
  {
    key: "model",
    label: "model",
    kind: "string",
    defaultValue: "deepseek-flash",
    required: true,
  },
  {
    key: "baseUrl",
    label: "baseUrl",
    kind: "string",
    defaultValue: "https://api.deepseek.com/v1",
    required: true,
  },
  {
    key: "api",
    label: "api",
    kind: "enum",
    defaultValue: "openai-completions",
    options: ["openai-completions", "anthropic-messages"],
  },
  {
    key: "effort",
    label: "effort",
    kind: "enum",
    defaultValue: "high",
    options: ["low", "medium", "high", "xhigh", "max"],
  },
  {
    key: "maxSteps",
    label: "maxSteps",
    kind: "number",
    defaultValue: "50",
    min: 1,
    max: 100,
    integer: true,
  },
  {
    key: "maxParallelTools",
    label: "maxParallelTools",
    kind: "number",
    defaultValue: "4",
    min: 1,
    max: 16,
    integer: true,
  },
  {
    key: "commandTimeoutSeconds",
    label: "commandTimeoutSeconds",
    kind: "number",
    defaultValue: "3600",
    min: 1,
    max: 3600,
  },
  {
    key: "approvalTimeoutSeconds",
    label: "approvalTimeoutSeconds",
    kind: "number",
    defaultValue: "120",
    min: 1,
    max: 3600,
  },
  {
    key: "contextWindowTokens",
    label: "contextWindowTokens",
    kind: "number",
    defaultValue: "1000000",
    min: 1024,
    max: 10000000,
    integer: true,
  },
  {
    key: "keepLastUserTurns",
    label: "keepLastUserTurns",
    kind: "number",
    defaultValue: "5",
    min: 0,
    max: 50,
    integer: true,
  },
  {
    key: "embeddingModel",
    label: "embeddingModel",
    kind: "string",
    defaultValue: "text-embedding-v4",
  },
  {
    key: "embeddingBaseUrl",
    label: "embeddingBaseUrl",
    kind: "string",
    defaultValue:
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
  },
  {
    key: "embeddingApiKey",
    label: "embeddingApiKey",
    kind: "secret",
    defaultValue: "",
  },
  {
    key: "computerUseEnabled",
    label: "computerUseEnabled",
    kind: "boolean",
    defaultValue: "true",
  },
  {
    key: "computerSessionTtlSeconds",
    label: "computerSessionTtlSeconds",
    kind: "number",
    defaultValue: "600",
    min: 30,
    max: 3600,
    integer: true,
  },
  {
    key: "computerSessionIdleSeconds",
    label: "computerSessionIdleSeconds",
    kind: "number",
    defaultValue: "90",
    min: 10,
    max: 3600,
    integer: true,
  },
  {
    key: "computerDeniedApps",
    label: "computerDeniedApps",
    kind: "string",
    defaultValue: "",
  },
  {
    key: "computerAuditEnabled",
    label: "computerAuditEnabled",
    kind: "boolean",
    defaultValue: "true",
  },
  {
    key: "browserBridgePort",
    label: "browserBridgePort",
    kind: "number",
    defaultValue: "17323",
    min: 0,
    max: 65535,
    integer: true,
  },
];

export function configValues(
  record: Record<string, unknown>,
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const field of CONFIG_FIELDS) {
    values[field.key] = configValue(field, record[field.key]);
  }
  return values;
}

function configValue(field: ConfigField, value: unknown): string {
  if (field.kind === "number") {
    return typeof value === "number" && Number.isFinite(value)
      ? String(value)
      : field.defaultValue;
  }
  if (field.kind === "boolean") {
    return typeof value === "boolean"
      ? String(value)
      : field.defaultValue;
  }
  return typeof value === "string" ? value : field.defaultValue;
}

export function parseConfigInput(
  field: ConfigField,
  raw: string,
  current: string,
): { value?: string; error?: string } {
  const text = raw.trim();
  if (field.kind === "secret") {
    return { value: text || current };
  }
  if (field.kind === "string") {
    if (field.required && !text) {
      return { error: `${field.label} must not be empty.` };
    }
    return { value: text };
  }
  if (field.kind === "enum") {
    const options = field.options ?? [];
    const match = options.find(
      (option) => option.toLowerCase() === text.toLowerCase(),
    );
    return match
      ? { value: match }
      : { error: `Choose one of: ${options.join(", ")}.` };
  }
  if (field.kind === "boolean") {
    const lowered = text.toLowerCase();
    if (["true", "yes", "1", "on"].includes(lowered)) {
      return { value: "true" };
    }
    if (["false", "no", "0", "off"].includes(lowered)) {
      return { value: "false" };
    }
    return { error: "Use true or false." };
  }
  const number = Number(text);
  if (
    !Number.isFinite(number) ||
    (field.integer && !Number.isInteger(number))
  ) {
    return {
      error: field.integer
        ? `${field.label} must be an integer.`
        : `${field.label} must be a number.`,
    };
  }
  if (
    (field.min !== undefined && number < field.min) ||
    (field.max !== undefined && number > field.max)
  ) {
    return {
      error: `${field.label} must be between ${field.min} and ${field.max}.`,
    };
  }
  return { value: String(number) };
}

export function buildConfigRecord(
  original: Record<string, unknown>,
  values: Record<string, string>,
): Record<string, unknown> {
  const record: Record<string, unknown> = { ...original };
  record.version = typeof original.version === "number" ? original.version : 1;
  for (const field of CONFIG_FIELDS) {
    const parsed = parseConfigInput(
      field,
      values[field.key] ?? "",
      field.defaultValue,
    );
    const value = parsed.value ?? field.defaultValue;
    if (field.kind === "number") {
      record[field.key] = Number(value);
    } else if (field.kind === "boolean") {
      record[field.key] = value === "true";
    } else {
      record[field.key] = value;
    }
  }
  return record;
}

export function configDisplayValue(
  field: ConfigField,
  value: string,
): string {
  if (field.kind === "secret") {
    return value ? "set" : "unset";
  }
  return value;
}

export function configHint(field: ConfigField): string {
  if (field.kind === "enum") {
    return `options: ${(field.options ?? []).join(" | ")}`;
  }
  if (field.kind === "boolean") {
    return "true | false";
  }
  if (field.kind === "number") {
    const range =
      field.min !== undefined && field.max !== undefined
        ? ` (${field.min}..${field.max})`
        : "";
    return `${field.integer ? "integer" : "number"}${range}`;
  }
  if (field.kind === "secret") {
    return "empty keeps the current value";
  }
  return field.required ? "required" : "optional";
}
