import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

import type { SidecarLaunch } from "./config.js";
import type { JsonObject, UiEvent } from "./types.js";

interface PendingRequest {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

export interface SidecarClientOptions {
  launch: SidecarLaunch;
  env: NodeJS.ProcessEnv;
  debugLog?: string;
  redact?: (text: string) => string;
}

export class SidecarClient extends EventEmitter {
  private readonly launch: SidecarLaunch;
  private readonly env: NodeJS.ProcessEnv;
  private readonly debugLog?: string;
  private readonly redact: (text: string) => string;
  private child?: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private readonly pending = new Map<number, PendingRequest>();
  private readonly stderrLines: string[] = [];
  private stdoutReader?: readline.Interface;
  private stderrReader?: readline.Interface;
  private debugStream?: fs.WriteStream;
  private closing = false;

  constructor(options: SidecarClientOptions) {
    super();
    this.launch = options.launch;
    this.env = options.env;
    this.debugLog = options.debugLog;
    this.redact = options.redact ?? ((text) => text);
    if (options.debugLog) {
      fs.mkdirSync(path.dirname(options.debugLog), { recursive: true });
      this.debugStream = fs.createWriteStream(options.debugLog, {
        flags: "a",
      });
    }
  }

  async start(): Promise<void> {
    if (this.child) {
      return;
    }
    const child = spawn(this.launch.command, this.launch.args, {
      cwd: this.launch.cwd,
      env: this.env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;

    this.stdoutReader = readline.createInterface({
      input: child.stdout,
      crlfDelay: Infinity,
      terminal: false,
    });
    this.stderrReader = readline.createInterface({
      input: child.stderr,
      crlfDelay: Infinity,
      terminal: false,
    });
    this.stdoutReader.on("line", (line) => this.handleLine(line));
    this.stderrReader.on("line", (line) => {
      const text = this.redact(line);
      this.stderrLines.push(text);
      if (this.stderrLines.length > 120) {
        this.stderrLines.shift();
      }
    });
    child.on("error", (error) => {
      this.failPending(error);
      this.emit("error", error);
    });
    child.on("exit", (code, signal) => {
      this.failPending(
        new Error(
          `Ling sidecar exited (code ${code ?? "null"}, signal ${
            signal ?? "null"
          }).`,
        ),
      );
      this.logDebug({ kind: "exit", code, signal });
      if (!this.closing) {
        this.emit("exit", { code, signal });
      }
    });

    await new Promise<void>((resolve, reject) => {
      child.once("spawn", () => resolve());
      child.once("error", (error) => reject(error));
    });
    this.logDebug({
      kind: "spawn",
      command: this.launch.command,
      args: this.launch.args,
      cwd: this.launch.cwd,
    });
  }

  private handleLine(line: string): void {
    const text = line.trim();
    if (!text) {
      return;
    }
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(text) as Record<string, unknown>;
    } catch {
      this.logDebug({ kind: "unparsed", line: text.slice(0, 500) });
      return;
    }
    if (
      typeof message.id === "number" &&
      ("result" in message || "error" in message)
    ) {
      const pending = this.pending.get(message.id);
      if (!pending) {
        return;
      }
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if ("error" in message) {
        const error = message.error as JsonObject | undefined;
        const detail = this.redact(
          String(error?.message ?? "JSON-RPC error"),
        );
        pending.reject(
          new Error(`${detail} (code ${String(error?.code ?? "?")})`),
        );
      } else {
        pending.resolve(message.result);
      }
      return;
    }
    if (message.method === "ui.event") {
      const params = message.params as JsonObject | undefined;
      const event = params?.event as UiEvent | undefined;
      if (event) {
        this.logDebug({ kind: "event", type: event.type });
        this.emit("event", event);
      }
      return;
    }
    if (message.method === "index.progress") {
      this.logDebug({ kind: "event", type: "index.progress" });
      this.emit("index-progress", message.params);
      return;
    }
    this.logDebug({ kind: "notification", method: String(message.method) });
    this.emit("notification", message);
  }

  call<T = unknown>(
    method: string,
    params: JsonObject = {},
    timeoutMs = 30_000,
  ): Promise<T> {
    const child = this.child;
    if (!child || child.stdin.destroyed) {
      return Promise.reject(new Error("Ling sidecar is not running."));
    }
    const id = this.nextId++;
    const started = Date.now();
    this.logDebug({ kind: "request", method, id });
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(
            `${method} timed out after ${Math.round(timeoutMs / 1000)}s.`,
          ),
        );
      }, timeoutMs);
      this.pending.set(id, {
        method,
        resolve: (value) => {
          this.logDebug({
            kind: "response",
            method,
            id,
            ms: Date.now() - started,
          });
          resolve(value as T);
        },
        reject,
        timer,
      });
      const payload = {
        jsonrpc: "2.0",
        id,
        method,
        params,
      };
      child.stdin.write(`${JSON.stringify(payload)}\n`, "utf8");
    });
  }

  onEvent(listener: (event: UiEvent) => void): () => void {
    this.on("event", listener);
    return () => this.off("event", listener);
  }

  stderrTail(lineCount = 30): string {
    return this.stderrLines.slice(-lineCount).join("\n");
  }

  async close(): Promise<void> {
    if (!this.child) {
      return;
    }
    const child = this.child;
    this.closing = true;
    if (child.exitCode === null) {
      try {
        await this.call("shutdown", {}, 5_000);
      } catch {
        // The sidecar may exit before answering shutdown.
      }
      await this.waitForExit(child, 8_000);
    }
    if (child.exitCode === null) {
      child.kill();
      await this.waitForExit(child, 5_000);
    }
    this.stdoutReader?.close();
    this.stderrReader?.close();
    this.stdoutReader = undefined;
    this.stderrReader = undefined;
    this.child = undefined;
    this.failPending(new Error("Ling sidecar was closed."));
    this.debugStream?.end();
    this.debugStream = undefined;
  }

  private waitForExit(
    child: ChildProcessWithoutNullStreams,
    timeoutMs: number,
  ): Promise<void> {
    if (child.exitCode !== null) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        child.off("exit", onExit);
        resolve();
      }, timeoutMs);
      const onExit = () => {
        clearTimeout(timer);
        resolve();
      };
      child.once("exit", onExit);
    });
  }

  private failPending(error: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this.pending.delete(id);
    }
  }

  private logDebug(payload: JsonObject): void {
    if (!this.debugStream) {
      return;
    }
    this.debugStream.write(
      `${JSON.stringify({ ts: Date.now(), ...payload })}\n`,
    );
  }
}
