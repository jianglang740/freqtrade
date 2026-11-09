import type { NextFunction, Request, Response } from 'express';
import { env } from '../env.js';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function sameOrigin(value: string): boolean {
  let origin: string;
  try {
    origin = new URL(value).origin;
  } catch {
    return false;
  }
  return env.ORIGINS.some((allowed) => allowed.toLowerCase() === origin.toLowerCase());
}

/**
 * CSRF 防线（配合 SameSite=Lax Cookie）：
 * - 非安全方法必须带正确的 Origin（缺失时退回校验 Referer）
 * - Sec-Fetch-Site 若存在，只允许同源/同站/none
 * - 有 body 的请求必须是 application/json，挡掉表单简单请求
 */
export function csrfGuard(req: Request, res: Response, next: NextFunction): void {
  if (SAFE_METHODS.has(req.method)) {
    next();
    return;
  }

  const origin = req.get('origin');
  const referer = req.get('referer');

  if (origin) {
    if (!sameOrigin(origin)) {
      res.status(403).json({ status: 'error', message: 'Request rejected (CSRF)' });
      return;
    }
  } else if (referer) {
    if (!sameOrigin(referer)) {
      res.status(403).json({ status: 'error', message: 'Request rejected (CSRF)' });
      return;
    }
  } else {
    res.status(403).json({ status: 'error', message: 'Request rejected (CSRF)' });
    return;
  }

  const fetchSite = req.get('sec-fetch-site');
  if (fetchSite && fetchSite !== 'same-origin' && fetchSite !== 'same-site' && fetchSite !== 'none') {
    res.status(403).json({ status: 'error', message: 'Request rejected (CSRF)' });
    return;
  }

  const hasBody = Number(req.get('content-length') || 0) > 0;
  if (hasBody && !req.is('application/json')) {
    res.status(403).json({ status: 'error', message: 'Request rejected (CSRF)' });
    return;
  }

  next();
}
