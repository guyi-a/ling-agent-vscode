import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SKILLS = [
  "pdf",
  "pptx",
  "docx",
  "inline-visualization",
  "browser-bridge",
  "frontend-design",
  "computer-use",
  "ling-self",
];

const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const source = resolve(
  process.env.LING_SKILLS_SOURCE?.trim() ||
    join(extensionRoot, "..", ".agents", "skills"),
);
const target = resolve(
  process.argv[2] || join(extensionRoot, "resources", "skills"),
);

if (!existsSync(source)) {
  console.error(`Skill source not found: ${source}`);
  process.exit(1);
}

mkdirSync(target, { recursive: true });

for (const name of SKILLS) {
  const from = join(source, name);
  const to = join(target, name);
  if (!existsSync(join(from, "SKILL.md"))) {
    console.error(`Missing SKILL.md for ${name}: ${from}`);
    process.exit(1);
  }
  rmSync(to, { recursive: true, force: true });
  cpSync(from, to, { recursive: true });
  console.log(`Synced skill ${name} -> ${to}`);
}
