import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Backend (FastAPI) serves the built index.html at "/" and mounts "/static".
// Build output goes directly into ../static so deployment needs no extra step.
export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  build: {
    outDir: "../static",
    // Old frontend files are removed manually; avoid wiping the shared
    // static/ directory from inside the build.
    emptyOutDir: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/chat": "http://127.0.0.1:8000",
      "/auth": "http://127.0.0.1:8000",
      "/threads": "http://127.0.0.1:8000",
      "/sessions": "http://127.0.0.1:8000",
      "/history": "http://127.0.0.1:8000",
      "/travel": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
