import assert from "node:assert/strict";
import path from "node:path";
import {
  computeProposedContent,
  resolveProposedPath,
} from "../src/diff/proposed";

const root = path.resolve("workspace");

assert.equal(
  resolveProposedPath(root, "src/app.ts"),
  path.join(root, "src", "app.ts"),
);
assert.equal(resolveProposedPath(root, "../escape.txt"), undefined);
assert.equal(resolveProposedPath(root, "C:\\absolute.txt"), undefined);
assert.equal(resolveProposedPath(root, "/absolute.txt"), undefined);
assert.equal(resolveProposedPath(root, ""), undefined);

assert.equal(
  computeProposedContent(
    "write_file",
    { path: "a.txt", content: "new content" },
    "old content",
  ),
  "new content",
);

assert.equal(
  computeProposedContent(
    "edit_file",
    { path: "a.txt", old_text: "alpha", new_text: "beta" },
    "alpha\nsecond alpha\n",
  ),
  "beta\nsecond alpha\n",
);

assert.equal(
  computeProposedContent(
    "edit_file",
    { path: "a.txt", old_text: "missing", new_text: "beta" },
    "alpha",
  ),
  undefined,
);

assert.equal(
  computeProposedContent(
    "write_file",
    { path: "a.txt", content: 42 },
    "alpha",
  ),
  undefined,
);

console.log("diff proposed-content tests passed");
