import type { NextFunction, Request, Response } from 'express';
import { COOKIE_NAME, verifySession } from '../services/auth.js';
import { getUserById, toPublicUser, type PublicUser } from '../services/users.js';

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      user?: PublicUser;
    }
  }
}

/**
 * 从 httpOnly Cookie 取会话 → 验 JWT → 回库取用户。
 * 角色、启用状态都以数据库为准，不信任 token 里的任何权限信息。
 */
export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const token = (req.cookies as Record<string, string | undefined> | undefined)?.[COOKIE_NAME];
  if (!token) {
    res.status(401).json({ status: 'error', message: 'Unauthorized' });
    return;
  }

  let userId: string;
  try {
    userId = verifySession(token);
  } catch {
    res.status(401).json({ status: 'error', message: 'Unauthorized' });
    return;
  }

  const user = getUserById(userId);
  if (!user || user.is_active !== 1) {
    res.status(401).json({ status: 'error', message: 'Unauthorized' });
    return;
  }

  req.user = toPublicUser(user);
  next();
}

/**
 * 只有 superadmin 能进。内容管理比机器人写权限更敏感，
 * 这里不看 CUSTOMER_READ_ONLY，角色必须是 superadmin。
 */
export function requireSuperadmin(req: Request, res: Response, next: NextFunction): void {
  if (req.user?.role === 'superadmin') {
    next();
    return;
  }
  res.status(403).json({ status: 'error', message: '只有管理员可以管理内容' });
}

/** 不强制登录，但如果有有效会话就把用户挂到 req.user（登出审计用） */
export function optionalAuth(req: Request, _res: Response, next: NextFunction): void {
  const token = (req.cookies as Record<string, string | undefined> | undefined)?.[COOKIE_NAME];
  if (token) {
    try {
      const user = getUserById(verifySession(token));
      if (user && user.is_active === 1) req.user = toPublicUser(user);
    } catch {
      // 无效会话按未登录处理
    }
  }
  next();
}
