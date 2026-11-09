import { Router } from 'express';
import type { NextFunction, Request, Response } from 'express';
import { requireAuth } from '../middleware/auth.js';
import { keyedLimiter } from '../middleware/rateLimit.js';
import { proxyBotLimiter, proxyUserLimiter } from '../services/rateLimit.js';
import { getBotForUser, type AccessResult } from '../services/bots.js';
import { proxyGet } from '../services/freqtrade.js';

const ACCESS_MESSAGES: Record<Exclude<AccessResult, { ok: true }>['reason'], string> = {
  not_found: 'Bot not found',
  forbidden: 'You do not have access to this bot',
  expired: 'Access to this bot has expired',
  disabled: 'This bot is disabled',
};

export const proxyRouter = Router();

// 鉴权在前，限流按用户：客户可能共用出口 IP，按 IP 卡会集体误伤
proxyRouter.use(requireAuth);
proxyRouter.use(keyedLimiter(proxyUserLimiter, (req) => req.user?.id ?? null));

proxyRouter.get('/:id/proxy/*', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const access = getBotForUser(req.user!, req.params.id);
    if (!access.ok) {
      const status = access.reason === 'not_found' ? 404 : 403;
      res.status(status).json({ status: 'error', message: ACCESS_MESSAGES[access.reason] });
      return;
    }

    // 每机器人次数上限（默认 240 次/分钟）：拿到 bot 之后才能按 bot id 计数
    const limit = proxyBotLimiter.check(access.bot.id);
    if (!limit.allowed) {
      res.setHeader('Retry-After', String(Math.max(1, Math.ceil(limit.retryAfterMs / 1000))));
      res.status(429).json({
        status: 'error',
        message: 'Too many requests, please try again later',
      });
      return;
    }

    const path = req.params[0] ?? '';
    const queryIndex = req.url.indexOf('?');
    const query = queryIndex >= 0 ? req.url.slice(queryIndex) : '';

    const result = await proxyGet(access.bot, path, query);
    if (!result.ok) {
      res.status(result.status).json({ status: 'error', message: result.message });
      return;
    }

    res.setHeader('Content-Type', result.contentType);
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Cache-Control', 'no-store');
    res.send(result.body);
  } catch (err) {
    next(err);
  }
});

/** 代理只开放 GET；写操作一律 405 */
proxyRouter.all('/:id/proxy/*', (_req: Request, res: Response) => {
  res.status(405).json({ status: 'error', message: 'Method not allowed' });
});
