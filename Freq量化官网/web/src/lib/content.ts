/*
 * 策略 / 因子内容的读写。
 *
 * 内容不在前端代码里，而是后端 content/ 目录下的 Markdown 文件：
 *   公开接口只返回 published 的条目；
 *   管理接口只有 superadmin 能用。
 *
 * 复用 api.ts 的 request()：Cookie 会话、CSRF、401 清理都走同一套。
 */

import { request } from './api';

export type ContentKind = 'strategies' | 'factors';

export type Metric = { label: string; value: string };

export type StrategyMeta = {
  slug: string;
  name: string;
  latin: string;
  tagline: string;
  desc: string;
  status: 'live' | 'paper';
  statusLabel: string;
  priceLabel?: string;
  metrics: Metric[];
  curve: number[] | null;
  curveNote: string;
  disclaimer?: string;
  published: boolean;
  order: number;
};

export type FactorMeta = {
  slug: string;
  name: string;
  latin: string;
  oneLine: string;
  status: '研究完成' | '研究中';
  published: boolean;
  order: number;
};

export type ContentMeta = StrategyMeta | FactorMeta;

export type ContentDoc<T extends ContentMeta> = { meta: T; body: string };

export type ContentSummary<T extends ContentMeta> = { meta: T; updatedAt: number };

/** 管理端列表项：解析失败的文件也列出来，方便定位 */
export type AdminItem = {
  slug: string;
  raw: string;
  updatedAt: number;
  meta?: ContentMeta;
  error?: string;
};

type ListResponse<T extends ContentMeta> = { items: ContentSummary<T>[] };

function isNotFound(err: unknown, kind: ContentKind): boolean {
  const expected = kind === 'strategies' ? '没有这个策略' : '没有这个因子';
  return err instanceof Error && err.message === expected;
}

export async function listStrategies(): Promise<StrategyMeta[]> {
  const res = await request<ListResponse<StrategyMeta>>('/content/strategies');
  return res.items.map((item) => item.meta);
}

export async function getStrategy(slug: string): Promise<ContentDoc<StrategyMeta> | null> {
  try {
    return await request<ContentDoc<StrategyMeta>>(
      `/content/strategies/${encodeURIComponent(slug)}`,
    );
  } catch (err) {
    if (isNotFound(err, 'strategies')) return null;
    throw err;
  }
}

export async function listFactors(): Promise<FactorMeta[]> {
  const res = await request<ListResponse<FactorMeta>>('/content/factors');
  return res.items.map((item) => item.meta);
}

export async function getFactor(slug: string): Promise<ContentDoc<FactorMeta> | null> {
  try {
    return await request<ContentDoc<FactorMeta>>(`/content/factors/${encodeURIComponent(slug)}`);
  } catch (err) {
    if (isNotFound(err, 'factors')) return null;
    throw err;
  }
}

/* ── 管理端 ── */

export async function adminListContent(): Promise<{
  strategies: AdminItem[];
  factors: AdminItem[];
}> {
  return request('/content/admin');
}

export async function saveContent(
  kind: ContentKind,
  slug: string,
  markdown: string,
): Promise<{ meta: ContentMeta; updatedAt: number }> {
  return request(`/content/admin/${kind}/${encodeURIComponent(slug)}`, {
    method: 'PUT',
    body: JSON.stringify({ markdown }),
  });
}

export async function deleteContent(kind: ContentKind, slug: string): Promise<void> {
  await request(`/content/admin/${kind}/${encodeURIComponent(slug)}`, { method: 'DELETE' });
}

/** 图片先转 base64 再走 JSON，后端的 CSRF 只接受 application/json */
export async function uploadAsset(file: File): Promise<{ url: string; bytes: number }> {
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(new Error('读取图片失败'));
    reader.readAsDataURL(file);
  });
  return request('/content/assets', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, contentType: file.type, data }),
  });
}
