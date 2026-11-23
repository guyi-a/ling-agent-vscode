const TARGETS = {
  "win32-x64": {
    nodePlatform: "win32",
    nodeArch: "x64",
    sidecarName: "ling-sidecar.exe",
  },
  "darwin-arm64": {
    nodePlatform: "darwin",
    nodeArch: "arm64",
    sidecarName: "ling-sidecar",
  },
};

export function currentPlatformTarget(
  platform = process.platform,
  arch = process.arch,
) {
  const target = `${platform}-${arch}`;
  if (!(target in TARGETS)) {
    throw new Error(
      `Unsupported build platform: ${platform}-${arch}. ` +
        `Supported targets: ${Object.keys(TARGETS).join(", ")}`,
    );
  }
  return target;
}

export function resolvePlatformTarget(value) {
  const target = value?.trim() || currentPlatformTarget();
  const config = TARGETS[target];
  if (!config) {
    throw new Error(
      `Unsupported VSIX target: ${target}. ` +
        `Supported targets: ${Object.keys(TARGETS).join(", ")}`,
    );
  }
  return {
    target,
    ...config,
  };
}

export function assertHostSupportsTarget(
  target,
  platform = process.platform,
  arch = process.arch,
) {
  if (platform !== target.nodePlatform || arch !== target.nodeArch) {
    throw new Error(
      `Cannot build ${target.target} on ${platform}-${arch}. ` +
        `Run it on ${target.nodePlatform}-${target.nodeArch}.`,
    );
  }
}

export function supportedTargets() {
  return Object.keys(TARGETS);
}
