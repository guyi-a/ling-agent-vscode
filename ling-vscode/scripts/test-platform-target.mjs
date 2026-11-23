import assert from "node:assert/strict";

import {
  assertHostSupportsTarget,
  currentPlatformTarget,
  resolvePlatformTarget,
  supportedTargets,
} from "./platform-target.mjs";

assert.deepEqual(supportedTargets(), ["win32-x64", "darwin-arm64"]);
assert.equal(currentPlatformTarget("win32", "x64"), "win32-x64");
assert.equal(currentPlatformTarget("darwin", "arm64"), "darwin-arm64");
assert.throws(() => currentPlatformTarget("darwin", "x64"));
assert.throws(() => currentPlatformTarget("linux", "x64"));

const windows = resolvePlatformTarget("win32-x64");
assert.equal(windows.sidecarName, "ling-sidecar.exe");
assert.doesNotThrow(() =>
  assertHostSupportsTarget(windows, "win32", "x64"),
);
assert.throws(() =>
  assertHostSupportsTarget(windows, "darwin", "arm64"),
);

const mac = resolvePlatformTarget("darwin-arm64");
assert.equal(mac.sidecarName, "ling-sidecar");
assert.doesNotThrow(() =>
  assertHostSupportsTarget(mac, "darwin", "arm64"),
);
assert.throws(() =>
  assertHostSupportsTarget(mac, "darwin", "x64"),
);

console.log("Platform target tests passed");
