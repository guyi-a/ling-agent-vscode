import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const lingRoot = resolve(extensionRoot, "..", "ling");
const outputDir =
  process.env.LING_SIDECAR_OUTPUT_DIR?.trim() ||
  join(extensionRoot, "resources", "sidecar");
const workDir = join(lingRoot, "build", "sidecar");
const specDir = join(lingRoot, "build");
const python =
  process.env.LING_PYTHON_PATH?.trim() ||
  (process.platform === "win32" ? "python" : "python3");
const binaryName =
  process.platform === "win32" ? "ling-sidecar.exe" : "ling-sidecar";
const binaryPath = join(outputDir, binaryName);

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
console.log(`Sidecar binary ready: ${binaryPath}`);
