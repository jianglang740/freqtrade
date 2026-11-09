import { Router } from 'express';
import type { NextFunction, Request, Response } from 'express';
import { z } from 'zod';
import { requireAuth, requireSuperadmin } from '../middleware/auth.js';
import { keyedLimiter } from '../middleware/rateLimit.js';
import { ApiError } from '../middleware/error.js';
import { apiUserLimiter, assetUploadLimiter } from '../services/rateLimit.js';
import { audit } from '../services/audit.js';
import {
  ContentError,
  MAX_MARKDOWN_BYTES,
  adminList,
  deleteContent,
  isContentKind,
  listContent,
  readContent,
  resolveAsset,
  storeAsset,
  writeContent,
  type ContentKind,
} from '../services/content.js';

const NOT_FOUND: Record<ContentKind, string> = {
  strategies: '没有这个策略',
  factors: '没有这个因子',
};

/** 把内容服务的业务错误转成统一的 ApiError，其余交给兜底错误处理 */
function toApiError(err: unknown): unknown {
  return err instanceof ContentError ? new ApiError(err.status, err.message) : err;
}

function listHandler(kind: ContentKind) {
  return (_req: Request, res: Response, next: NextFunction): void => {
    try {
      // 上传后刷新就要能看到，所以只做协商缓存，不做强缓存
      res.setHeader('Cache-Control', 'no-cache');
      res.json({ items: listContent(kind) });
    } catch (err) {
      next(toApiError(err));
    }
  };
}

function detailHandler(kind: ContentKind) {
  return (req: Request, res: Response, next: NextFunction): void => {
    try {
      const item = readContent(kind, req.params.slug ?? '');
      if (!item) throw new ApiError(404, NOT_FOUND[kind]);
      res.setHeader('Cache-Control', 'no-cache');
      res.json({ meta: item.meta, body: item.body });
    } catch (err) {
      next(toApiError(err));
    }
  };
}

const saveSchema = z.object({ markdown: z.string().min(1) }).strict();

const uploadSchema = z
  .object({
    filename: z.string().max(200).optional(),
    contentType: z.string().max(100).optional(),
    data: z.string().min(1),
  })
  .strict();

const adminLimiter = keyedLimiter(apiUserLimiter, (req) => req.user?.id ?? null);
const uploadLimiter = keyedLimiter(assetUploadLimiter, (req) => req.user?.id ?? null);

export const contentRouter = Router();

/* ── 公开读取：策略与因子 ── */

contentRouter.get('/content/strategies', listHandler('strategies'));
contentRouter.get('/content/strategies/:slug', detailHandler('strategies'));
contentRouter.get('/content/factors', listHandler('factors'));
contentRouter.get('/content/factors/:slug', detailHandler('factors'));

/* ── 公开图片：内容哈希命名，可以永久缓存 ── */

contentRouter.get('/content/assets/:name', (req: Request, res: Response) => {
  const full = resolveAsset(req.params.name ?? '');
  if (!full) {
    res.status(404).json({ status: 'error', message: 'Not found' });
    return;
  }
  res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.sendFile(full, (err) => {
    if (err && !res.headersSent) res.status(404).end();
  });
});

/* ── 管理端：仅 superadmin ── */

contentRouter.get(
  '/content/admin',
  requireAuth,
  requireSuperadmin,
  (_req: Request, res: Response) => {
    res.setHeader('Cache-Control', 'no-store');
    res.json({
      strategies: adminList('strategies'),
      factors: adminList('factors'),
    });
  },
);

contentRouter.put(
  '/content/admin/:kind/:slug',
  requireAuth,
  requireSuperadmin,
  adminLimiter,
  (req: Request, res: Response, next: NextFunction) => {
    try {
      const kind = req.params.kind ?? '';
      if (!isContentKind(kind)) throw new ApiError(404, 'Not found');
      const body = saveSchema.parse(req.body ?? {});
      if (Buffer.byteLength(body.markdown, 'utf8') > MAX_MARKDOWN_BYTES) {
        throw new ApiError(400, '文件超过 512KB');
      }
      const existed = readContent(kind, req.params.slug ?? '', { includeDrafts: true }) !== null;
      const saved = writeContent(kind, req.params.slug ?? '', body.markdown);
      audit({
        actorId: req.user!.id,
        action: 'content_saved',
        target: `${kind}/${req.params.slug}`,
        detail: existed ? 'updated' : 'created',
        ip: req.ip,
      });
      res.json({ meta: saved.meta, updatedAt: saved.updatedAt });
    } catch (err) {
      next(toApiError(err));
    }
  },
);

contentRouter.delete(
  '/content/admin/:kind/:slug',
  requireAuth,
  requireSuperadmin,
  adminLimiter,
  (req: Request, res: Response, next: NextFunction) => {
    try {
      const kind = req.params.kind ?? '';
      if (!isContentKind(kind)) throw new ApiError(404, 'Not found');
      deleteContent(kind, req.params.slug ?? '');
      audit({
        actorId: req.user!.id,
        action: 'content_deleted',
        target: `${kind}/${req.params.slug}`,
        ip: req.ip,
      });
      res.status(204).end();
    } catch (err) {
      next(toApiError(err));
    }
  },
);

contentRouter.post(
  '/content/assets',
  requireAuth,
  requireSuperadmin,
  uploadLimiter,
  (req: Request, res: Response, next: NextFunction) => {
    try {
      const body = uploadSchema.parse(req.body ?? {});
      const asset = storeAsset(body.data);
      audit({
        actorId: req.user!.id,
        action: 'asset_uploaded',
        target: asset.url,
        detail: `${asset.bytes} bytes`,
        ip: req.ip,
      });
      res.status(201).json(asset);
    } catch (err) {
      next(toApiError(err));
    }
  },
);
