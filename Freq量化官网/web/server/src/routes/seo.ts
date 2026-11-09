/*
 * 站点地图与 robots.txt。
 *
 * 不写死域名：优先用请求自身的 origin（Nginx 会转发 Host 和 X-Forwarded-Proto，
 * 前提是 TRUST_PROXY=true），拿不到时退回 PUBLIC_ORIGINS 的第一条。
 * 策略 / 因子的详情页从 content/ 目录现读，后台新增一篇，这里自动出现。
 */

import { Router } from 'express';
import type { Request, Response } from 'express';
import { env } from '../env.js';
import { listContent, type ContentKind } from '../services/content.js';

export const seoRouter = Router();

function baseUrl(req: Request): string {
  const host = req.get('host');
  if (host) return `${req.protocol}://${host}`;
  return env.PUBLIC_ORIGINS.split(',')[0].trim().replace(/\/+$/, '');
}

const XML_ESCAPE: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&apos;',
};

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => XML_ESCAPE[c]);
}

type Url = {
  loc: string;
  lastmod?: string;
  changefreq?: string;
  priority: string;
};

function contentUrls(kind: ContentKind, base: string): Url[] {
  return listContent(kind).map(({ meta, updatedAt }) => ({
    loc: `${base}/${kind}/${meta.slug}`,
    lastmod: new Date(updatedAt).toISOString().slice(0, 10),
    changefreq: 'monthly',
    priority: '0.7',
  }));
}

seoRouter.get('/sitemap.xml', (req: Request, res: Response) => {
  const base = baseUrl(req);
  const urls: Url[] = [
    { loc: `${base}/`, changefreq: 'weekly', priority: '1.0' },
    { loc: `${base}/strategies`, changefreq: 'weekly', priority: '0.9' },
    { loc: `${base}/factors`, changefreq: 'weekly', priority: '0.8' },
    { loc: `${base}/app`, changefreq: 'monthly', priority: '0.8' },
    ...contentUrls('strategies', base),
    ...contentUrls('factors', base),
  ];

  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls.map((u) =>
      [
        '  <url>',
        `    <loc>${escapeXml(u.loc)}</loc>`,
        u.lastmod ? `    <lastmod>${u.lastmod}</lastmod>` : '',
        u.changefreq ? `    <changefreq>${u.changefreq}</changefreq>` : '',
        `    <priority>${u.priority}</priority>`,
        '  </url>',
      ]
        .filter(Boolean)
        .join('\n'),
    ),
    '</urlset>',
    '',
  ].join('\n');

  res
    .status(200)
    .setHeader('Content-Type', 'application/xml; charset=utf-8')
    .setHeader('Cache-Control', 'public, max-age=3600')
    .send(body);
});

seoRouter.get('/robots.txt', (req: Request, res: Response) => {
  const body = [
    'User-agent: *',
    'Allow: /',
    // 仪表盘与后台不该被搜索引擎收录
    'Disallow: /robots',
    'Disallow: /admin',
    'Disallow: /api/',
    '',
    `Sitemap: ${baseUrl(req)}/sitemap.xml`,
    '',
  ].join('\n');

  res
    .status(200)
    .setHeader('Content-Type', 'text/plain; charset=utf-8')
    .setHeader('Cache-Control', 'public, max-age=3600')
    .send(body);
});
