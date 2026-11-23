import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertHostSupportsTarget,
  resolvePlatformTarget,
} from "./platform-target.mjs";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");

function readTargetArg(argv) {
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--target") {
      return argv[index + 1];
    }
    if (value.startsWith("--target=")) {
      return value.slice("--target=".length);
    }
  }
  return process.env.LING_VSIX_TARGET;
}

const target = resolvePlatformTarget(readTargetArg(process.argv.slice(2)));
assertHostSupportsTarget(target);
const sidecarPath = resolve(
  extensionRoot,
  "resources",
  "sidecar",
  target.sidecarName,
);
if (!existsSync(sidecarPath)) {
  throw new Error(
    `Missing ${target.sidecarName}. Run npm run build:sidecar ` +
      `with LING_SIDECAR_TARGET=${target.target} first.`,
  );
}

const result = spawnSync(
  "npx",
  [
    "@vscode/vsce",
    "package",
    "--no-dependencies",
    "--target",
    target.target,
  ],
  {
    cwd: extensionRoot,
    shell: process.platform === "win32",
    stdio: "inherit",
  },
);

if (result.error) {
  console.error(`Unable to run vsce: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
