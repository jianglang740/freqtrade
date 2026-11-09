import type { NextFunction, Request, Response } from 'express';
import { env } from '../env.js';

export const READ_ONLY_MESSAGE = '只读面板：当前账号没有写权限，如需调整请联系服务方';

/**
 * 写权限闸门：CUSTOMER_READ_ONLY=true 时只有 superadmin 能写。
 * 这是客户只读面板的最后一道防线，前端隐藏按钮不算数。
 */
export function requireWriteAccess(req: Request, res: Response, next: NextFunction): void {
  if (!env.CUSTOMER_READ_ONLY) {
    next();
    return;
  }
  if (req.user?.role === 'superadmin') {
    next();
    return;
  }
  res.status(403).json({ status: 'error', message: READ_ONLY_MESSAGE });
}
