import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // 5173 is the origin the backend's CORS_ORIGINS allows, so keep it fixed.
  server: { port: 5173, strictPort: true },
});
