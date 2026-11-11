/*
 * 策略 / 因子内容库：Markdown 文件即内容。
 *
 * 目录结构：
 *   content/strategies/<slug>.md
 *   content/factors/<slug>.md
 *   content/assets/<sha256 前 16 位>.<png|jpg|webp|gif>
 *   content/_trash/…            删除只移动到这里，不物理删除
 *
 * 为什么不用数据库：内容量很小、要能被 git 直接跟踪、用户想看到文件本身。
 * 写入是原子替换（先写 .tmp 再 rename），读的时候按需要校验，坏文件不会拖垮列表。
 */
import { createHash } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import path from 'node:path';
import YAML from 'yaml';
import { z } from 'zod';
import { env } from '../env.js';

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const MAX_MARKDOWN_BYTES = 512 * 1024;
const MAX_ASSET_BYTES = 8 * 1024 * 1024;
const ASSET_NAME_RE = /^[a-f0-9]{16}\.(png|jpg|webp|gif)$/;

export type ContentKind = 'strategies' | 'factors';

export function isContentKind(value: string): value is ContentKind {
  return value === 'strategies' || value === 'factors';
}

/** 业务错误：status 直接透传给前端，message 允许展示 */
export class ContentError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const metricSchema = z
  .object({
    label: z.string().trim().min(1).max(32),
    value: z.string().trim().min(1).max(64),
  })
  .strict();

const baseMeta = {
  slug: z.string().trim().regex(SLUG_RE, '只能包含小写字母、数字和连字符'),
  name: z.string().trim().min(1).max(200),
  latin: z.string().trim().min(1).max(64),
  published: z.boolean().default(true),
  order: z.number().int().min(0).max(9999).default(100),
};

export const strategyMetaSchema = z
  .object({
    ...baseMeta,
    tagline: z.string().trim().min(1).max(500),
    desc: z.string().trim().min(1).max(1000),
    status: z.enum(['live', 'paper']),
    statusLabel: z.string().trim().min(1).max(32).optional(),
    priceLabel: z.string().trim().min(1).max(64).optional(),
    metrics: z.array(metricSchema).max(6).default([]),
    curve: z.array(z.number()).max(5000).nullable().default(null),
    curveNote: z.string().trim().max(500).default(''),
    disclaimer: z.string().trim().max(2000).optional(),
  })
  .strict();

export const factorMetaSchema = z
  .object({
    ...baseMeta,
    oneLine: z.string().trim().min(1).max(500),
    status: z.enum(['研究完成', '研究中']),
  })
  .strict();

export type StrategyMeta = z.infer<typeof strategyMetaSchema>;
export type FactorMeta = z.infer<typeof factorMetaSchema>;
export type ContentMeta = StrategyMeta | FactorMeta;

export type ContentItem<T extends ContentMeta = ContentMeta> = {
  meta: T;
  body: string;
  raw: string;
  updatedAt: number;
};

export type ContentSummary<T extends ContentMeta = ContentMeta> = {
  meta: T;
  updatedAt: number;
};

export type AdminItem = {
  slug: string;
  raw: string;
  updatedAt: number;
  meta?: ContentMeta;
  error?: string;
};

function kindDir(kind: ContentKind): string {
  return path.join(env.CONTENT_DIR, kind);
}

const assetsDir = path.join(env.CONTENT_DIR, 'assets');
const trashDir = path.join(env.CONTENT_DIR, '_trash');

function ensureDirs(): void {
  mkdirSync(kindDir('strategies'), { recursive: true });
  mkdirSync(kindDir('factors'), { recursive: true });
  mkdirSync(assetsDir, { recursive: true });
  mkdirSync(trashDir, { recursive: true });
}
ensureDirs();

/** 由 slug 拼出绝对路径；slug 已经过正则约束，这里再断言一次前缀 */
function fileFor(kind: ContentKind, slug: string): string {
  if (!SLUG_RE.test(slug)) {
    throw new ContentError(400, 'slug 只能包含小写字母、数字和连字符');
  }
  const dir = path.resolve(kindDir(kind));
  const target = path.resolve(dir, `${slug}.md`);
  if (path.dirname(target) !== dir) {
    throw new ContentError(400, '非法的 slug');
  }
  return target;
}

export function splitFrontMatter(raw: string): { data: unknown; body: string } {
  const text = raw.replace(/^﻿/, '');
  const lines = text.split(/\r?\n/);
  if (lines[0]?.trim() !== '---') {
    throw new ContentError(400, '文件必须以 --- 开头，并用 --- 包住 front matter');
  }
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
  if (end < 0) {
    throw new ContentError(400, 'front matter 缺少结束的 ---');
  }

  let data: unknown;
  try {
    data = YAML.parse(lines.slice(1, end).join('\n'));
  } catch (err) {
    throw new ContentError(
      400,
      `front matter 不是合法的 YAML：${err instanceof Error ? err.message : '解析失败'}`,
    );
  }

  const body = lines
    .slice(end + 1)
    .join('\n')
    .replace(/^\n+/, '')
    .replace(/\s+$/, '\n');
  return { data, body };
}

/** 把 zod 的英文报错换成能直接展示给用户的中文 */
function formatZodError(err: z.ZodError): string {
  const parts = err.issues.slice(0, 6).map((issue) => {
    const field = issue.path.join('.') || 'front matter';
    switch (issue.code) {
      case z.ZodIssueCode.invalid_enum_value:
        return `${field} 只能是 ${issue.options.join(' / ')}`;
      case z.ZodIssueCode.invalid_type:
        return `${field} 类型不正确（需要 ${issue.expected}）`;
      case z.ZodIssueCode.invalid_string:
        return `${field} 格式不正确`;
      case z.ZodIssueCode.too_small:
        return `${field} 太小或太短`;
      case z.ZodIssueCode.too_big:
        return `${field} 太大或太长`;
      case z.ZodIssueCode.unrecognized_keys:
        return `不认识的字段：${issue.keys.join('、')}`;
      case z.ZodIssueCode.invalid_union:
        return `${field} 取值不合法`;
      default:
        return `${field}: ${issue.message}`;
    }
  });
  return `front matter 校验失败：${parts.join('；')}`;
}

function parseMeta(kind: ContentKind, data: unknown): ContentMeta {
  const schema = kind === 'strategies' ? strategyMetaSchema : factorMetaSchema;
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new ContentError(400, formatZodError(result.error));
  }
  const meta = result.data;
  if (kind === 'strategies') {
    const strategy = meta as StrategyMeta;
    return {
      ...strategy,
      statusLabel:
        strategy.statusLabel ?? (strategy.status === 'live' ? '已上线' : '模拟盘观察中'),
    };
  }
  return meta;
}

export function parseContent(
  kind: ContentKind,
  raw: string,
  expectedSlug: string,
): { meta: ContentMeta; body: string } {
  if (Buffer.byteLength(raw, 'utf8') > MAX_MARKDOWN_BYTES) {
    throw new ContentError(400, '文件超过 512KB');
  }
  const { data, body } = splitFrontMatter(raw);
  const meta = parseMeta(kind, data);
  if (meta.slug !== expectedSlug) {
    throw new ContentError(
      400,
      `front matter.slug（${meta.slug}）必须与文件名（${expectedSlug}）保持一致`,
    );
  }
  return { meta, body };
}

function sortItems<T extends ContentSummary>(items: T[]): T[] {
  return items.sort((a, b) => {
    const orderDiff = (a.meta.order ?? 100) - (b.meta.order ?? 100);
    if (orderDiff !== 0) return orderDiff;
    return a.meta.name.localeCompare(b.meta.name, 'zh-Hans-CN');
  });
}

/** 列表只读 front matter；单个文件坏了就跳过并在服务端日志里留痕 */
export function listContent(
  kind: ContentKind,
  options: { includeDrafts?: boolean } = {},
): ContentSummary[] {
  const dir = kindDir(kind);
  const items: ContentSummary[] = [];
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.md')) continue;
    const slug = file.slice(0, -3);
    if (!SLUG_RE.test(slug)) continue;
    const full = path.join(dir, file);
    try {
      const raw = readFileSync(full, 'utf8');
      const { meta } = parseContent(kind, raw, slug);
      if (!options.includeDrafts && meta.published === false) continue;
      items.push({ meta, updatedAt: statSync(full).mtimeMs });
    } catch (err) {
      console.error(
        `[content] 跳过 ${kind}/${file}：${err instanceof Error ? err.message : '解析失败'}`,
      );
    }
  }
  return sortItems(items);
}

export function readContent(
  kind: ContentKind,
  slug: string,
  options: { includeDrafts?: boolean } = {},
): ContentItem | null {
  const full = fileFor(kind, slug);
  if (!existsSync(full)) return null;
  const raw = readFileSync(full, 'utf8');
  const { meta, body } = parseContent(kind, raw, slug);
  if (!options.includeDrafts && meta.published === false) return null;
  return { meta, body, raw, updatedAt: statSync(full).mtimeMs };
}

/** 新建和更新都走这里：校验 → 写临时文件 → 原子替换 */
export function writeContent(kind: ContentKind, slug: string, markdown: string): ContentSummary {
  const full = fileFor(kind, slug);
  const { meta } = parseContent(kind, markdown, slug);
  const normalized = markdown.endsWith('\n') ? markdown : `${markdown}\n`;
  const tmp = `${full}.${process.pid}.tmp`;
  writeFileSync(tmp, normalized, 'utf8');
  renameSync(tmp, full);
  return { meta, updatedAt: statSync(full).mtimeMs };
}

/** 删除 = 移到 _trash，误删还能找回 */
export function deleteContent(kind: ContentKind, slug: string): void {
  const full = fileFor(kind, slug);
  if (!existsSync(full)) {
    throw new ContentError(404, '内容不存在');
  }
  const stamp = new Date()
    .toISOString()
    .replace(/[-:T]/g, '')
    .slice(0, 14);
  renameSync(full, path.join(trashDir, `${stamp}-${kind}-${slug}.md`));
}

/** 管理页需要看到草稿，也要能看到“有问题的文件”，所以不跳过解析失败项 */
export function adminList(kind: ContentKind): AdminItem[] {
  const dir = kindDir(kind);
  const items: AdminItem[] = [];
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.md')) continue;
    const slug = file.slice(0, -3);
    if (!SLUG_RE.test(slug)) continue;
    const full = path.join(dir, file);
    const raw = readFileSync(full, 'utf8');
    const updatedAt = statSync(full).mtimeMs;
    try {
      const { meta } = parseContent(kind, raw, slug);
      items.push({ slug, raw, updatedAt, meta });
    } catch (err) {
      items.push({
        slug,
        raw,
        updatedAt,
        error: err instanceof Error ? err.message : '解析失败',
      });
    }
  }
  return items.sort((a, b) => {
    const orderDiff = (a.meta?.order ?? 100) - (b.meta?.order ?? 100);
    if (orderDiff !== 0) return orderDiff;
    return a.slug.localeCompare(b.slug);
  });
}

function detectImage(buf: Buffer): 'png' | 'jpg' | 'webp' | 'gif' | null {
  if (buf.length > 8 && buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) {
    return 'png';
  }
  if (buf.length > 3 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return 'jpg';
  if (buf.length > 6 && buf.toString('ascii', 0, 4) === 'GIF8') return 'gif';
  if (
    buf.length > 12 &&
    buf.toString('ascii', 0, 4) === 'RIFF' &&
    buf.toString('ascii', 8, 12) === 'WEBP'
  ) {
    return 'webp';
  }
  return null;
}

/** 图片以内容哈希命名：同一张图重复上传不会产生新文件，也能放心加 immutable 缓存 */
export function storeAsset(dataBase64: string): { url: string; bytes: number } {
  const cleaned = dataBase64.replace(/^data:[^;]+;base64,/, '');
  let buf: Buffer;
  try {
    buf = Buffer.from(cleaned, 'base64');
  } catch {
    throw new ContentError(400, '图片内容不是合法的 base64');
  }
  if (buf.length === 0) {
    throw new ContentError(400, '图片内容为空');
  }
  if (buf.length > MAX_ASSET_BYTES) {
    throw new ContentError(400, '图片超过 8MB');
  }
  // 只认文件头，不认扩展名和浏览器声明的 Content-Type；SVG/HTML 会在这里被拒
  const ext = detectImage(buf);
  if (!ext) {
    throw new ContentError(400, '只支持 PNG / JPEG / WebP / GIF 图片');
  }
  const name = `${createHash('sha256').update(buf).digest('hex').slice(0, 16)}.${ext}`;
  const full = path.join(assetsDir, name);
  if (!existsSync(full)) {
    writeFileSync(full, buf);
  }
  return { url: `/api/content/assets/${name}`, bytes: buf.length };
}

/** 只返回白名单命名的已存在文件；路径由文件名拼出，不接受任何目录成分 */
export function resolveAsset(name: string): string | null {
  if (!ASSET_NAME_RE.test(name)) return null;
  const full = path.join(assetsDir, name);
  return existsSync(full) ? full : null;
}

export { MAX_MARKDOWN_BYTES, MAX_ASSET_BYTES };
