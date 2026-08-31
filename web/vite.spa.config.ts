import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "spa",
  publicDir: "../public",
  plugins: [react()],
  build: {
    outDir: "../../src/adsb_ingest/web_static",
    emptyOutDir: true,
  },
});
