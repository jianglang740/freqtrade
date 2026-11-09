import { Router } from 'express';
import type { CookieOptions } from 'express';
import { z } from 'zod';
import { env } from '../env.js';
import { optionalAuth, requireAuth } from '../middleware/auth.js';
import { ipLimiter } from '../middleware/rateLimit.js';
import { ApiError } from '../middleware/error.js';
import { COOKIE_NAME, login, signSession } from '../services/auth.js';
import { loginLimiter } from '../services/rateLimit.js';
import { audit } from '../services/audit.js';
import { getUserByUsername } from '../services/users.js';

const loginSchema = z
  .object({
    username: z.string().trim().min(1).max(64),
    password: z.string().min(1).max(256),
  })
  .strict();

function cookieOptions(maxAge?: number): CookieOptions {
  return {
    httpOnly: true,
    sameSite: 'lax',
    secure: env.COOKIE_SECURE,
    path: '/',
    ...(maxAge === undefined ? {} : { maxAge }),
  };
}

export const authRouter = Router();

authRouter.post('/login', ipLimiter(loginLimiter), async (req, res, next) => {
  try {
    const body = loginSchema.parse(req.body ?? {});
    const ip = req.ip || req.socket.remoteAddress || null;
    const dbUser = getUserByUsername(body.username);

    let user;
    try {
      user = await login(dbUser, body.password, ip);
    } catch (err) {
      audit({
        actorId: dbUser?.id ?? null,
        action: 'login_failed',
        target: body.username,
        ip,
      });
      throw new ApiError(401, err instanceof Error ? err.message : 'Login failed');
    }

    const token = signSession(user.id);
    res.cookie(COOKIE_NAME, token, cookieOptions(env.SESSION_HOURS * 3600 * 1000));
    audit({ actorId: user.id, action: 'login', ip });
    res.json({ user });
  } catch (err) {
    next(err);
  }
});

/** 登出只需清 Cookie；即使没有会话也幂等返回 204 */
authRouter.post('/logout', optionalAuth, (req, res) => {
  res.clearCookie(COOKIE_NAME, cookieOptions());
  if (req.user) audit({ actorId: req.user.id, action: 'logout', ip: req.ip });
  res.status(204).end();
});

authRouter.get('/me', requireAuth, (req, res) => {
  res.json(req.user);
});
