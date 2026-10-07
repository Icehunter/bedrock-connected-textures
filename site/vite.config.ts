import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/bedrock-connected-textures/",
  plugins: [react(), tailwindcss()],
  server: { fs: { allow: [".."] } },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});
