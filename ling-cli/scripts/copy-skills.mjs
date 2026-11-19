import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(scriptDir, "..");
const repoRoot = path.resolve(packageRoot, "..");
const source = path.join(repoRoot, ".agents", "skills");
const target = path.join(packageRoot, "skills");

if (!fs.existsSync(source)) {
  console.error(`Missing built-in skills: ${source}`);
  process.exit(1);
}

fs.rmSync(target, { recursive: true, force: true });
fs.cpSync(source, target, { recursive: true });
const count = fs
  .readdirSync(target, { withFileTypes: true })
  .filter((entry) => entry.isDirectory()).length;
console.log(`Copied ${count} skill directories -> ${target}`);
