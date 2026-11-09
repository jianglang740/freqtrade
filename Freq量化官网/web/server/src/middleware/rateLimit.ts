import type { NextFunction, Request, Response } from 'express';
import type { FixedWindowLimiter } from '../services/rateLimit.js';

export type LimiterReply = (req: Request) => void;

function reject(res: Response, retryAfterMs: number): void {
  res.setHeader('Retry-After', String(Math.max(1, Math.ceil(retryAfterMs / 1000))));
  // 文案保留英文，前端会统一翻译成「请求过于频繁，请稍后再试」
  res.status(429).json({ status: 'error', message: 'Too many requests, please try again later' });
}

/** 按 IP 限流的中间件 */
export function ipLimiter(limiter: FixedWindowLimiter) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const result = limiter.check(req.ip || req.socket.remoteAddress || 'unknown');
    if (!result.allowed) {
      reject(res, result.retryAfterMs);
      return;
    }
    next();
  };
}

/** 按自定义 key 限流，key 由调用方在拿到 bot/用户后再决定 */
export function keyedLimiter(
  limiter: FixedWindowLimiter,
  keyOf: (req: Request) => string | null,
) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const key = keyOf(req);
    if (key) {
      const result = limiter.check(key);
      if (!result.allowed) {
        reject(res, result.retryAfterMs);
        return;
      }
    }
    next();
  };
}
