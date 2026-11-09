import { defineConfig } from 'vite';
import type { Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/*
 * 分享图必须是绝对地址（微信、X 等抓取方不认相对路径）。
 * 域名不写死在仓库里：部署时用 SITE_URL=https://你的域名 npm run build。
 * 不传这个变量也没关系，保持相对地址，浏览器里照样能显示。
 */
function shareImageUrl(): Plugin {
  const siteUrl = (process.env.SITE_URL ?? '').trim().replace(/\/+$/, '');
  return {
    name: 'freq-share-image-url',
    transformIndexHtml(html) {
      if (!siteUrl) return html;
      return html
        .replaceAll('content="/og.png"', `content="${siteUrl}/og.png"`)
        .replace('</head>', `    <meta property="og:url" content="${siteUrl}/" />\n  </head>`);
    },
  };
}

// 官网前端：静态页面 + 「我的机器人」代理到官网自己的后端（web/server，3002）
export default defineConfig({
  plugins: [react(), shareImageUrl()],
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
