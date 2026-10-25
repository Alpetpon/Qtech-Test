import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // npm run dev: запросы к API уходят в бэкенд из docker compose
  server: { proxy: { "/api": "http://localhost:8000" } },
});
