import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(packageRoot, "..");
const source = path.join(
  repoRoot,
  "ling-vscode",
  "resources",
  "sidecar",
  "ling-sidecar.exe",
);
const targetDir = path.join(packageRoot, "vendor", "win32-x64");
const target = path.join(targetDir, "ling-sidecar.exe");

if (!fs.existsSync(source)) {
  console.error(
    `Missing sidecar executable: ${source}\n` +
      "Build it first:\n" +
      "  cd ling-vscode\n" +
      "  npm run build:sidecar",
  );
  process.exit(1);
}

fs.mkdirSync(targetDir, { recursive: true });
fs.copyFileSync(source, target);
const size = fs.statSync(target).size;
console.log(
  `Copied sidecar (${(size / 1024 / 1024).toFixed(1)} MB) -> ${target}`,
);
