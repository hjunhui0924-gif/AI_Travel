import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Backend (FastAPI) serves the built index.html at "/" and mounts "/static".
// Port 8000 may be used by another local project; keep this travel backend on
// 8001 so the proxy cannot silently talk to the wrong application.
// Build output goes directly into ../static so deployment needs no extra step.
export default defineConfig({
  plugins: [vue()],
  // Read the project-level .env so frontend-only VITE_* values can be kept
  // beside the backend configuration. Vite exposes only prefixed variables.
  envDir: "..",
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
      "/chat": "http://127.0.0.1:8001",
      "/auth": "http://127.0.0.1:8001",
      "/threads": "http://127.0.0.1:8001",
      "/sessions": "http://127.0.0.1:8001",
      "/history": "http://127.0.0.1:8001",
      "/travel": "http://127.0.0.1:8001",
      "/health": "http://127.0.0.1:8001",
    },
  },
});
