import { existsSync } from 'node:fs';
import path from 'node:path';
import express from 'express';
import type { NextFunction, Request, Response } from 'express';
import compression from 'compression';
import cookieParser from 'cookie-parser';
import helmet from 'helmet';
import { env } from './env.js';
import { csrfGuard } from './middleware/csrf.js';
import { ipLimiter } from './middleware/rateLimit.js';
import { apiIpLimiter } from './services/rateLimit.js';
import { apiNotFound, errorHandler } from './middleware/error.js';
import { authRouter } from './routes/auth.js';
import { botsRouter } from './routes/bots.js';
import { proxyRouter } from './routes/proxy.js';
import { contentRouter } from './routes/content.js';
import { usersRouter } from './routes/users.js';
import { seoRouter } from './routes/seo.js';

export const app = express();

app.disable('x-powered-by');
if (env.TRUST_PROXY) app.set('trust proxy', 1);

app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'", "'unsafe-inline'"],
        imgSrc: ["'self'", 'data:'],
        fontSrc: ["'self'", 'data:'],
        connectSrc: ["'self'"],
        frameAncestors: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
        objectSrc: ["'none'"],
        // 生产走 HTTPS 时强制升级；开发/内网 http 下必须关掉，否则静态资源会被改写成 https 而全部失败
        upgradeInsecureRequests: env.NODE_ENV === 'production' ? [] : null,
      },
    },
    crossOriginResourcePolicy: { policy: 'same-origin' },
    referrerPolicy: { policy: 'no-referrer' },
    frameguard: { action: 'deny' },
  }),
);

app.use(cookieParser());
// 内容管理：Markdown 与 base64 图片比分页大，单独放宽；其余 /api 仍是 100kb
app.use(['/api/content/admin', '/api/content/assets'], express.json({ limit: '12mb' }));
app.use(express.json({ limit: '100kb', strict: true }));
// 代理返回的 JSON 动辄几十 KB，gzip 后能省 70%+ 带宽
app.use(compression({ threshold: 1024 }));

// 只记录方法、路径、状态、耗时、用户；不记录请求体与响应体。
// 默认只记错误与慢请求：高并发下逐条 stdout 本身就会拖慢事件循环。
app.use((req: Request, res: Response, next: NextFunction) => {
  const start = Date.now();
  res.on('finish', () => {
    const ms = Date.now() - start;
    if (!env.LOG_REQUESTS && res.statusCode < 400 && ms < 1000) return;
    console.log(
      `[http] ${req.method} ${req.path} ${res.statusCode} ${ms}ms ` +
        `user=${req.user?.id ?? '-'} ip=${req.ip}`,
    );
  });
  next();
});

app.get('/api/healthz', (_req, res) => {
  res.json({ status: 'ok' });
});

// /api 的 IP 兜底（很高上限）→ CSRF → 业务路由；用户级限流在各自路由里做
app.use('/api', ipLimiter(apiIpLimiter));
app.use('/api', csrfGuard);
app.use('/api/auth', authRouter);
app.use('/api/bots', botsRouter);
app.use('/api/bots', proxyRouter);
app.use('/api/users', usersRouter);
app.use('/api', contentRouter);
app.use('/api', apiNotFound);

// SEO 文件必须在静态托管之前：它们要按请求域名动态生成
app.use(seoRouter);

// 生产模式：同一个进程托管前端构建产物，未命中的非 /api 路径回落到 index.html
if (existsSync(path.join(env.STATIC_DIR, 'index.html'))) {
  app.use(
    express.static(env.STATIC_DIR, {
      index: false,
      maxAge: '1h',
      setHeaders: (res, filePath) => {
        if (filePath.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache');
      },
    }),
  );
  app.get('*', (req, res, next) => {
    if (req.path.startsWith('/api/')) {
      next();
      return;
    }
    res.sendFile(path.join(env.STATIC_DIR, 'index.html'));
  });
}

app.use(errorHandler);
