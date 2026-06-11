import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build output is committed into the FastAPI app (src/app/static/admin) and
// served by main.py's StaticFiles mount at /admin — keeps Render's build
// pip-only. `base` must match the mount path or asset URLs 404.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/admin/",
  build: {
    outDir: "../src/app/static/admin",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      // Local dev: `npm run dev` + `uvicorn app.main:app --port 8000`
      "/api": "http://localhost:8000",
    },
  },
});
