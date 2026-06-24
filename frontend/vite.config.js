import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5181,
    strictPort: true,
    host: true, // bind to all interfaces so ngrok can forward
    allowedHosts: ['.ngrok-free.app', '.ngrok-free.dev', '.ngrok.app', '.ngrok.io',
                   '.trycloudflare.com', '.loca.lt', 'localhost', '127.0.0.1'],
    proxy: { '/api': { target: 'http://127.0.0.1:8031', changeOrigin: true } },
  },
})
