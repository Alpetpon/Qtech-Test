import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const { APP_PORT } = loadEnv(mode, "..", "APP_PORT");
  return {
    plugins: [react()],
    server: { proxy: { "/api": `http://localhost:${APP_PORT || "8000"}` } },
  };
});
