import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 官网前端：静态页面 + 「我的机器人」代理到官网自己的后端（web/server，3002）
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 4000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:3002',
        changeOrigin: true,
      },
    },
  },
});
