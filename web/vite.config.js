import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs on :8000 (uvicorn app.main:app). In production FastAPI serves web/dist itself, so calls are same-origin.
const API = ['/health', '/meta', '/model', '/agent', '/cases', '/outbox', '/actions', '/automation', '/galaxy', '/twin', '/simulate', '/predict', '/explain', '/score']

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: Object.fromEntries(API.map(p => [p, 'http://127.0.0.1:8000'])) },
  test: { environment: 'node' },
})
