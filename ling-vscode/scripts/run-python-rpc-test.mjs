import { spawn } from "node:child_process";
import { resolve } from "node:path";

const projectRoot = resolve(process.cwd(), "..", "ling");
const python = process.env.LING_PYTHON_PATH || "python";
const child = spawn(
  python,
  [
    "-W",
    "error",
    "-m",
    "unittest",
    "tests.test_ling_rpc",
    "tests.test_ling_rpc_sessions",
    "tests.test_ling_rpc_background_tasks",
    "tests.test_ling_comment",
    "-v",
  ],
  {
    cwd: projectRoot,
    stdio: "inherit",
    windowsHide: true,
  },
);

child.on("error", (error) => {
  console.error(`Unable to start Python tests: ${error.message}`);
  process.exitCode = 1;
});

child.on("exit", (code, signal) => {
  if (code !== 0) {
    console.error(
      `Python RPC tests failed (code=${code ?? "null"}, signal=${signal ?? "null"})`,
    );
    process.exitCode = code ?? 1;
  }
});
