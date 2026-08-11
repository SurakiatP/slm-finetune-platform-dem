import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const engineHost = env.VITE_ENGINE_HOST ?? "http://10.222.43.195:8080";

  return {
  server: {
    host: "::",
    port: 5173,
    hmr: {
      overlay: false,
    },
    proxy: {
      // Forward API calls and WebSocket to the Engine backend
      "/api/v1": {
        target: engineHost,
        changeOrigin: true,
        secure: false,
      },
      "/ws/": {
        target: engineHost,
        changeOrigin: true,
        ws: true,
        secure: false,
      },
      "/health": {
        target: engineHost,
        changeOrigin: true,
        secure: false,
      },
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  };
});
