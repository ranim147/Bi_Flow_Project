import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En développement, l'API FastAPI tourne sur :8000 (python -m biflow.api)
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
