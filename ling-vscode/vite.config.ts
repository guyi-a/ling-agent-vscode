import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// Windows resolves paths case-insensitively, but Rollup does not. Mixing
// "e:/..." and "E:/..." module ids makes it bundle two copies of React.
// Pin one drive-letter casing for every path derived from __dirname.
const projectRoot =
  __dirname.charAt(0).toUpperCase() + __dirname.slice(1);

function fromRoot(...parts: string[]): string {
  return resolve(projectRoot, ...parts);
}

export default defineConfig({
  root: fromRoot("webview"),
  base: "./",
  plugins: [react()],
  resolve: {
    alias: [
      {
        find: /^react$/,
        replacement: fromRoot("node_modules/react/index.js"),
      },
      {
        find: /^react\/jsx-runtime$/,
        replacement: fromRoot("node_modules/react/jsx-runtime.js"),
      },
      {
        find: /^react\/jsx-dev-runtime$/,
        replacement: fromRoot("node_modules/react/jsx-dev-runtime.js"),
      },
      {
        find: /^react-dom$/,
        replacement: fromRoot("node_modules/react-dom/index.js"),
      },
      {
        find: /^react-dom\/client$/,
        replacement: fromRoot("node_modules/react-dom/client.js"),
      },
    ],
    dedupe: ["react", "react-dom"],
  },
  build: {
    outDir: fromRoot("media/webview"),
    emptyOutDir: true,
  },
  server: {
    open: true,
    fs: {
      allow: [projectRoot],
    },
  },
});
