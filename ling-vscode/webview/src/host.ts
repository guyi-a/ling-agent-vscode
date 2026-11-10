import {
  isHostToWebviewMessage,
  type HostToWebviewMessage,
  type WebviewToHostMessage,
} from "../../shared/transport";

export type HostKind = "browser" | "vscode";

interface VsCodeApi {
  postMessage(message: unknown): void;
  getState<T>(): T | undefined;
  setState<T>(state: T): void;
}

declare global {
  interface Window {
    acquireVsCodeApi?: () => VsCodeApi;
  }
}

export interface HostBridge {
  readonly kind: HostKind;
  postMessage(message: WebviewToHostMessage): void;
  subscribe(listener: (message: HostToWebviewMessage) => void): () => void;
}

let cachedVsCodeApi: VsCodeApi | undefined;

export function createHostBridge(): HostBridge {
  if (typeof window.acquireVsCodeApi === "function") {
    cachedVsCodeApi ??= window.acquireVsCodeApi();

    return {
      kind: "vscode",
      postMessage: (message) => cachedVsCodeApi?.postMessage(message),
      subscribe: (listener) => {
        const handleMessage = (event: MessageEvent<unknown>) => {
          if (isHostToWebviewMessage(event.data)) {
            listener(event.data);
          }
        };

        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
      },
    };
  }

  return {
    kind: "browser",
    postMessage: (message) => {
      console.info("[Ling preview]", message);
    },
    subscribe: () => () => undefined,
  };
}
