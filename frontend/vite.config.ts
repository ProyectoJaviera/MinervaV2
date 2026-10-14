import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Subfase D (plan pre-Fase 5 aprobado 2026-10-14): desarrollo SIEMPRE contra
// scripts/run_dev_server.py en el puerto 8001 (DB temporal) -- nunca el bot
// real del puerto 8000. Todos los endpoints nuevos viven bajo /api/*
// (protegidos con cookie httpOnly); /health sigue sin auth.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8001", changeOrigin: true },
      "/health": "http://localhost:8001",
    },
  },
});
