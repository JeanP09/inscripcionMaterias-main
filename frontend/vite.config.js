import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En desarrollo (npm run dev) el frontend usa rutas relativas /api y Vite
// las reenvía al backend local, replicando el comportamiento de Nginx en
// Docker. Así el MISMO código funciona en ambos entornos.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
