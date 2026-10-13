import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: the console talks only to the control plane (docs/02 section 3)
const CONTROL_API = process.env.PP_SWICH_CONTROL_URL ?? "http://127.0.0.1:8911";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: CONTROL_API, changeOrigin: true },
      "/healthz": { target: CONTROL_API, changeOrigin: true },
    },
  },
});
