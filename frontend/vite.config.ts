import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Proyecto minimo de Fase 1: sin auth, sin WebSocket todavia (polling a la
// API REST). El dashboard completo (Tailwind, graficos, tiempo real,
// autenticacion) es Fase 5 -- ver docs/FASE0.md.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/positions": "http://localhost:8000",
      "/trades": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
