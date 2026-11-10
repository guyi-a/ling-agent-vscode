import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: "webview",
  base: "./",
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "media/webview"),
    emptyOutDir: true,
  },
  server: {
    open: true,
    fs: {
      allow: [resolve(__dirname)],
    },
  },
});
