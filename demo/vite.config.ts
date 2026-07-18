import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: the demo runs on :5173, Odoo on :8069. Proxying /web and
// /ncollection makes Odoo appear same-origin to the browser, so the
// session_id cookie is set and sent automatically and CORS never applies.
const ODOO_URL = 'http://localhost:8069';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true,
    proxy: {
      '/web': { target: ODOO_URL, changeOrigin: true },
      '/ncollection': { target: ODOO_URL, changeOrigin: true },
    },
  },
});
