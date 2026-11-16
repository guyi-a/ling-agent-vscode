export type ApiProtocol =
  | "openai-completions"
  | "anthropic-messages";

export const DEFAULT_API_PROTOCOL: ApiProtocol = "openai-completions";

export interface ApiProtocolDefaults {
  model: string;
  baseUrl: string;
}

export const API_PROTOCOL_DEFAULTS: Record<
  ApiProtocol,
  ApiProtocolDefaults
> = {
  "openai-completions": {
    model: "deepseek-flash",
    baseUrl: "https://api.deepseek.com/v1",
  },
  "anthropic-messages": {
    model: "deepseek-flash",
    baseUrl: "https://api.deepseek.com/anthropic",
  },
};

export interface ApiProtocolOption {
  value: ApiProtocol;
  label: string;
}

export const API_PROTOCOL_OPTIONS: ApiProtocolOption[] = [
  {
    value: "openai-completions",
    label: "OpenAI Chat Completions",
  },
  {
    value: "anthropic-messages",
    label: "Anthropic Messages",
  },
];

export function isApiProtocol(value: unknown): value is ApiProtocol {
  return (
    value === "openai-completions" ||
    value === "anthropic-messages"
  );
}
