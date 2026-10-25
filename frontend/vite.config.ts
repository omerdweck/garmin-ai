import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the page runs on Vite (localhost:5173) and /api is
// forwarded to a local webapp process, so the browser sees one origin -
// exactly like production, where Caddy does the same forwarding.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8001" },
  },
  build: {
    outDir: "dist",
    // Phones on mobile data: no source maps shipped, small chunks.
    sourcemap: false,
  },
});
