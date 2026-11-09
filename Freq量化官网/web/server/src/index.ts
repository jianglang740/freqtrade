import { app } from './app.js';
import { db } from './db/index.js';
import { env } from './env.js';

const server = app.listen(env.PORT, env.HOST, () => {
  console.log(`[server] listening on http://${env.HOST}:${env.PORT} (${env.NODE_ENV})`);
  console.log(`[server] static dir: ${env.STATIC_DIR}`);
});

// 空闲长连接保留 65s（要大于 Nginx upstream keepalive 的 60s，避免偶发 502）；
// headersTimeout 必须大于 keepAliveTimeout；一个请求最多 30s 收完。
server.keepAliveTimeout = 65_000;
server.headersTimeout = 66_000;
server.requestTimeout = 30_000;

let shuttingDown = false;
function shutdown(signal: string): void {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[server] ${signal} received, shutting down`);

  // 先断开空闲的 keep-alive 连接，否则 close() 要等它们自然到期
  server.closeIdleConnections();
  server.close(() => {
    try {
      db.close();
    } catch {
      // 关闭失败也不影响进程退出
    }
    process.exit(0);
  });

  // 超过 5 秒还没关干净就强制退出
  setTimeout(() => process.exit(1), 5_000).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
