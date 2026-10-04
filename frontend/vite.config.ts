import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, run the backend with `auto-poster --dev --no-browser` and open http://localhost:5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
