import type { NextFunction, Request, Response } from 'express';
import { ZodError } from 'zod';

/** 业务错误：message 是允许暴露给前端的文案，绝不带内部细节 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** 统一错误出口：只返回 {status, error}，不返回堆栈与 SQL */
export function errorHandler(
  err: unknown,
  _req: Request,
  res: Response,
  _next: NextFunction,
): void {
  if (res.headersSent) return;

  if (err instanceof ApiError) {
    res.status(err.status).json({ status: 'error', error: err.message, message: err.message });
    return;
  }

  if (err instanceof ZodError) {
    const message = err.issues[0]?.message || 'Invalid request';
    res.status(400).json({ status: 'error', error: message, message });
    return;
  }

  // body-parser 的体积/JSON 错误：不处理会掉进兜底变成 500
  const type = (err as { type?: string } | null)?.type;
  if (type === 'entity.too.large') {
    res.status(413).json({ status: 'error', message: '请求体过大' });
    return;
  }
  if (type === 'entity.parse.failed') {
    res.status(400).json({ status: 'error', message: 'JSON 格式错误' });
    return;
  }

  // 兜底：不把内部错误原文吐给客户端，只在服务端日志里留一句
  console.error('[error]', err instanceof Error ? err.message : 'unknown');
  res.status(500).json({ status: 'error', message: 'Internal server error' });
}

/** /api 下未命中的路径统一返回 JSON 404 */
export function apiNotFound(_req: Request, res: Response): void {
  res.status(404).json({ status: 'error', message: 'Not found' });
}
