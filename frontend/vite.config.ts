import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const projectDir = fileURLToPath(new URL(".", import.meta.url));

function readVersion() {
  try {
    return readFileSync(resolve(projectDir, "../VERSION"), "utf8").trim() || "0.0.0";
  } catch {
    return "0.0.0";
  }
}

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(projectDir, "./src"),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION || readVersion()),
  },
  server: {
    host: "0.0.0.0",
    port: 4399,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/auth": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/v1": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/images": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/image-thumbnails": { target: "http://127.0.0.1:8002", changeOrigin: false },
      "/avatars": { target: "http://127.0.0.1:8002", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
