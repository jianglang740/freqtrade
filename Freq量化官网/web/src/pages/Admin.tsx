/*
 * 内容管理：superadmin 直接在网页里编辑策略 / 因子的 Markdown 文件。
 *
 * 保存后立刻写进 server/content/*.md，前台刷新就能看到，不需要重新构建或部署。
 * 前端隐藏入口只是方便，真正的权限门在后端 requireSuperadmin。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AdminGate } from '../components/AdminGate';
import { ArticleMarkdown } from '../components/ArticleMarkdown';
import type { SiteUser } from '../lib/api';
import {
  adminListContent,
  deleteContent,
  saveContent,
  uploadAsset,
  type AdminItem,
  type ContentKind,
} from '../lib/content';

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

const KIND_LABEL: Record<ContentKind, string> = {
  strategies: '策略',
  factors: '因子',
};

function template(kind: ContentKind, slug: string): string {
  if (kind === 'strategies') {
    return [
      '---',
      `slug: ${slug}`,
      'name: 未命名策略',
      'latin: NEW-STRATEGY',
      'tagline: 一句话说明这个策略做什么',
      'desc: 卡片上显示的两三行描述。',
      'status: paper',
      'statusLabel: 模拟盘观察中',
      'metrics:',
      '  - label: 年化',
      '    value: 待填',
      '  - label: 最大回撤',
      '    value: 待填',
      '  - label: 夏普',
      '    value: 待填',
      'curve: null',
      'curveNote: 暂无公开曲线',
      'published: false',
      'order: 100',
      '---',
      '',
      '这里开始写正文。章节用 `## 标题`，公式或伪代码用 ``` 代码块，表格用 GFM 表格。',
      '',
    ].join('\n');
  }

  return [
    '---',
    `slug: ${slug}`,
    'name: 未命名因子',
    'latin: NEW-FACTOR',
    'oneLine: 一句话说明这个因子在衡量什么。',
    'status: 研究中',
    'published: false',
    'order: 100',
    '---',
    '',
    '## 定义',
    '',
    '## 公式',
    '',
    '```text',
    '',
    '```',
    '',
    '## 怎么用',
    '',
    '- ',
    '',
    '## 限制与坑',
    '',
  ].join('\n');
}

/** 预览时先丢掉 front matter；这里只做分割，不解析 YAML */
function stripFrontMatter(src: string): string {
  const m = src.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return m ? src.slice(m[0].length) : src;
}

/** 从文件名推一个合法 slug，用户之后可以改 */
function slugFromFilename(name: string): string {
  const base = name.replace(/\.(md|markdown|txt)$/i, '').toLowerCase();
  return base
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

export function Admin() {
  return (
    <AdminGate
      kicker="FREQ / 内容管理"
      title="管理员登录"
      lead="策略与因子内容只允许管理员维护。登录后编辑保存，前台刷新即可看到。"
      denied="只有管理员可以管理内容。"
    >
      {(user, logout) => <AdminConsole user={user} onLogout={logout} />}
    </AdminGate>
  );
}

function AdminConsole({ user, onLogout }: { user: SiteUser; onLogout: () => void }) {
  const [kind, setKind] = useState<ContentKind>('strategies');
  const [lists, setLists] = useState<{ strategies: AdminItem[]; factors: AdminItem[] }>({
    strategies: [],
    factors: [],
  });
  const [listLoading, setListLoading] = useState(true);

  const [slug, setSlug] = useState('');
  const [source, setSource] = useState('');
  const [savedSource, setSavedSource] = useState('');
  const [isNew, setIsNew] = useState(false);

  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState(true);
  const [dragging, setDragging] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const dirty = source !== savedSource;
  const items = lists[kind];

  const loadList = async () => {
    setListLoading(true);
    try {
      const data = await adminListContent();
      setLists(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取内容列表失败');
    } finally {
      setListLoading(false);
    }
  };

  useEffect(() => {
    void loadList();
  }, []);

  // 关标签页前提醒未保存
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (source !== savedSource) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [source, savedSource]);

  const resetEditor = () => {
    setSlug('');
    setSource('');
    setSavedSource('');
    setIsNew(false);
    setError(null);
    setNotice(null);
  };

  const select = (item: AdminItem) => {
    if (dirty && !window.confirm('当前修改还没保存，确定切换吗？')) return;
    setSlug(item.slug);
    setSource(item.raw);
    setSavedSource(item.raw);
    setIsNew(false);
    setError(null);
    setNotice(item.error ? `这个文件有问题：${item.error}` : null);
  };

  const startNew = () => {
    if (dirty && !window.confirm('当前修改还没保存，确定新建吗？')) return;
    const used = new Set(items.map((item) => item.slug));
    let next = 'new-item';
    let n = 2;
    while (used.has(next)) {
      next = `new-item-${n}`;
      n += 1;
    }
    setSlug(next);
    setSource(template(kind, next));
    setSavedSource('');
    setIsNew(true);
    setError(null);
    setNotice(null);
  };

  /** 新建时改 slug 要同步改 front matter，否则保存会报 slug 与文件名不一致 */
  const changeSlug = (next: string) => {
    setSlug(next);
    if (isNew) {
      setSource((s) => s.replace(/^slug:\s*.*$/m, `slug: ${next}`));
    }
  };

  const switchKind = (next: ContentKind) => {
    if (next === kind) return;
    if (dirty && !window.confirm('当前修改还没保存，确定切换分类吗？')) return;
    setKind(next);
    resetEditor();
  };

  const insertAtCursor = (text: string) => {
    const el = textareaRef.current;
    if (!el) {
      setSource((s) => `${s}\n\n${text}\n`);
      return;
    }
    const start = el.selectionStart ?? source.length;
    const end = el.selectionEnd ?? start;
    const next = source.slice(0, start) + text + source.slice(end);
    setSource(next);
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + text.length;
      el.setSelectionRange(pos, pos);
    });
  };

  const uploadImages = async (files: File[]) => {
    for (const file of files) {
      if (!IMAGE_TYPES.includes(file.type)) {
        setError('只支持 PNG / JPEG / WebP / GIF 图片');
        continue;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        setError(`图片 ${file.name} 超过 8MB`);
        continue;
      }
      setUploading(true);
      try {
        const { url } = await uploadAsset(file);
        insertAtCursor(`![${file.name.replace(/[[\]]/g, '')}](${url})`);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : '上传图片失败');
      } finally {
        setUploading(false);
      }
    }
  };

  const loadMarkdownFile = async (file: File) => {
    if (dirty && !window.confirm('当前修改还没保存，确定用拖入的文件替换吗？')) return;
    const text = await file.text();
    if (isNew || !slug) {
      const nextSlug = slugFromFilename(file.name);
      if (nextSlug && SLUG_RE.test(nextSlug)) setSlug(nextSlug);
    }
    setSource(text);
    setSavedSource(isNew ? '' : savedSource);
    setError(null);
    setNotice(`已载入 ${file.name}，确认无误后点击保存`);
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    const images = files.filter((f) => f.type.startsWith('image/'));
    const docs = files.filter((f) => /\.(md|markdown|txt)$/i.test(f.name));
    if (images.length) await uploadImages(images);
    if (docs.length) await loadMarkdownFile(docs[0]);
  };

  const save = async () => {
    if (!SLUG_RE.test(slug)) {
      setError('slug 只能包含小写字母、数字和连字符，且不能以连字符开头');
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await saveContent(kind, slug, source);
      setSavedSource(source);
      setIsNew(false);
      setNotice('已保存。前台刷新页面即可看到。');
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确定删除 ${KIND_LABEL[kind]}「${slug}」吗？文件会移到 _trash，之后可以手动找回。`)) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await deleteContent(kind, slug);
      resetEditor();
      setNotice('已删除。');
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setBusy(false);
    }
  };

  const previewBody = useMemo(() => stripFrontMatter(source), [source]);
  const current = items.find((item) => item.slug === slug);

  return (
    <section className="wrap console admin-console">
      <aside className="sidebar">
        <div className="side-brand">
          <span className="side-brand__mark">＋</span>
          <span>
            FREQ<span className="side-brand__dim"> / 内容</span>
          </span>
        </div>

        <nav className="side-nav">
          {(['strategies', 'factors'] as ContentKind[]).map((k) => (
            <button
              key={k}
              type="button"
              className={`side-btn${kind === k ? ' is-active' : ''}`}
              onClick={() => switchKind(k)}
            >
              <span className="side-btn__idx">{k === 'strategies' ? '01' : '02'}</span>
              <span>{KIND_LABEL[k]}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="side-label-row">
            <span className="side-label">{KIND_LABEL[kind]}</span>
            <button type="button" className="side-add" onClick={startNew} title="新建">
              ＋ 新建
            </button>
          </div>

          {listLoading ? (
            <div className="side-user">读取中…</div>
          ) : items.length === 0 ? (
            <div className="side-user">还没有内容</div>
          ) : (
            items.map((item) => (
              <button
                key={item.slug}
                type="button"
                className={`side-btn side-bot${item.slug === slug ? ' is-active' : ''}`}
                onClick={() => select(item)}
                title={item.meta?.name ?? item.slug}
              >
                <i className={`dot${item.error ? ' dot--off' : ''}`} />
                <span className="side-bot__name">{item.meta?.name ?? item.slug}</span>
                {item.meta?.published === false && <span className="admin-badge">草稿</span>}
                {item.error && <span className="admin-badge">错误</span>}
              </button>
            ))
          )}

          <div className="side-user side-login">已登录：{user.username}</div>
          <button type="button" className="btn btn--sm side-exit" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </aside>

      <div className="console-main">
        <div className="hub-head">
          <div>
            <h1 className="hub-head__title">内容管理</h1>
            <p className="hub-head__sub">
              编辑 server/content/{kind}/*.md，保存后前台刷新即生效
            </p>
          </div>
          <div className="hub-head__tools">
            <Link className="hub-refresh" to={kind === 'strategies' ? '/strategies' : '/factors'}>
              查看前台 ↗
            </Link>
            <button type="button" className="hub-refresh" onClick={() => void loadList()}>
              ↻ 刷新
            </button>
          </div>
        </div>

        <div className="admin-toolbar">
          <label className="admin-slug">
            <span className="metric-label">slug（文件名）</span>
            <input
              className="input"
              value={slug}
              onChange={(e) => changeSlug(e.target.value)}
              placeholder="例如 xsec-momentum"
              disabled={!isNew}
            />
          </label>

          <div className="admin-actions">
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => void save()}
              disabled={busy || !slug || !dirty}
            >
              {busy ? '处理中…' : dirty ? '保存' : '已保存'}
            </button>

            <button
              type="button"
              className="btn btn--sm"
              onClick={() => fileRef.current?.click()}
              disabled={uploading || !slug}
            >
              {uploading ? '上传中…' : '插入图片'}
            </button>

            <button type="button" className="btn btn--sm" onClick={() => setPreview((v) => !v)}>
              {preview ? '隐藏预览' : '显示预览'}
            </button>

            <button
              type="button"
              className="btn btn--sm"
              onClick={() => void remove()}
              disabled={busy || isNew || !current}
            >
              删除
            </button>
          </div>

          <input
            ref={fileRef}
            type="file"
            accept={IMAGE_TYPES.join(',')}
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = '';
              void uploadImages(files);
            }}
          />
        </div>

        {error && (
          <p className="admin-msg admin-msg--error" role="alert">
            {error}
          </p>
        )}
        {notice && !error && <p className="admin-msg">{notice}</p>}

        <div className={`admin-layout${preview ? '' : ' admin-layout--single'}`}>
          <div
            className="admin-editor"
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => void onDrop(e)}
          >
            <textarea
              ref={textareaRef}
              className="input admin-source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              spellCheck={false}
              placeholder="选择左侧的文件，或点击「新建」。也可以把 .md 文件或图片直接拖进来。"
            />
            <div className={`admin-drop${dragging ? ' is-active' : ''}`} aria-hidden="true">
              松开以载入 Markdown 或上传图片
            </div>
          </div>

          {preview && (
            <div className="admin-preview article">
              {source.trim() ? (
                <ArticleMarkdown>{previewBody}</ArticleMarkdown>
              ) : (
                <div className="empty">预览区</div>
              )}
            </div>
          )}
        </div>

        <details className="admin-help">
          <summary>front matter 速查</summary>
          <div className="article" style={{ paddingTop: 12 }}>
            <p>
              <strong>策略</strong>：<code>slug</code>（必须与文件名一致）、<code>name</code>、
              <code>latin</code>、<code>tagline</code>、<code>desc</code>、
              <code>status</code>（<code>live</code> 或 <code>paper</code>）、
              <code>statusLabel</code>、<code>metrics</code>（最多 6 条 label/value）、
              <code>curve</code>（数字数组，或 <code>null</code>）、<code>curveNote</code>、
              <code>disclaimer</code>、<code>published</code>、<code>order</code>。
            </p>
            <p>
              <strong>因子</strong>：<code>slug</code>、<code>name</code>、<code>latin</code>、
              <code>oneLine</code>、<code>status</code>（<code>研究完成</code> 或{' '}
              <code>研究中</code>）、<code>published</code>、<code>order</code>。
            </p>
            <p>
              <code>published: false</code> 是草稿：前台列表与详情都看不到，管理页会标「草稿」。
              <code>order</code> 越小越靠前，同序号按名称排。
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
