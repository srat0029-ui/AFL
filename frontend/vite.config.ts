import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Honor an externally-assigned port (e.g. from a dev-server launcher) when present,
    // falling back to Vite's default for plain `npm run dev`.
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
  },
})
