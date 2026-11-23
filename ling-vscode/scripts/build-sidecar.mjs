import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertHostSupportsTarget,
  resolvePlatformTarget,
} from "./platform-target.mjs";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const lingRoot = resolve(extensionRoot, "..", "ling");
const target = resolvePlatformTarget(
  process.env.LING_SIDECAR_TARGET,
);
assertHostSupportsTarget(target);
const outputDir =
  process.env.LING_SIDECAR_OUTPUT_DIR?.trim() ||
  join(extensionRoot, "resources", "sidecar");
const workDir = join(lingRoot, "build", "sidecar");
const specDir = join(lingRoot, "build");
const python =
  process.env.LING_PYTHON_PATH?.trim() ||
  (process.platform === "win32" ? "python" : "python3");
const binaryPath = join(outputDir, target.sidecarName);

if (!existsSync(join(lingRoot, "ling_rpc"))) {
  console.error(`Ling sidecar source not found: ${lingRoot}`);
  process.exit(1);
}
mkdirSync(outputDir, { recursive: true });

const result = spawnSync(
  python,
  [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "ling-sidecar",
    "--paths",
    lingRoot,
    "--collect-all",
    "pypdf",
    "--collect-all",
    "websockets",
    "--collect-submodules",
    "mcp.client",
    "--collect-submodules",
    "mcp.shared",
    ...(process.platform === "win32"
      ? [
          "--collect-submodules",
          "comtypes",
          "--hidden-import",
          "comtypes.client",
          "--hidden-import",
          "comtypes.gen",
          "--collect-all",
          "PIL",
        ]
      : []),
    "--distpath",
    outputDir,
    "--workpath",
    workDir,
    "--specpath",
    specDir,
    join(lingRoot, "ling_sidecar_entry.py"),
  ],
  { cwd: lingRoot, stdio: "inherit" },
);

if (result.status !== 0) {
  console.error("PyInstaller failed. Install it with: pip install pyinstaller");
  process.exit(result.status ?? 1);
}
if (!existsSync(binaryPath)) {
  console.error(`Expected sidecar binary was not created: ${binaryPath}`);
  process.exit(1);
}
if (process.platform !== "win32") {
  chmodSync(binaryPath, 0o755);
}
console.log(`Sidecar binary ready: ${binaryPath}`);
