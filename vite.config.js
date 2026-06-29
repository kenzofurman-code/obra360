import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ['video.js', 'videojs-vr', 'hls.js', 'three']
  }
})
