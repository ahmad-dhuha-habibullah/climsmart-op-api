import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.CLIMSMART_API ?? "http://127.0.0.1:8077";
const proxy = { "/api": { target: api, changeOrigin: true } };

// Built into ../public/app so Vercel serves it at /app next to the existing v1 UI (which stays at /).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "../public/app", emptyOutDir: true, chunkSizeWarningLimit: 2500 },
  server: { port: 5174, proxy },
  preview: { port: 5173, proxy },
});
