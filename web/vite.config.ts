/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本地开发时将 /api 代理到 API 服务；可用 API_PORT 覆盖宿主端口
const apiTarget = `http://localhost:${process.env.API_PORT ?? 8000}`;

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
