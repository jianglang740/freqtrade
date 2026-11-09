/*
 * Markdown 正文渲染。
 *
 * 安全边界：
 * - 不挂 rehype-raw，并显式 skipHtml：正文里的 <script>/<iframe> 一律当纯文本丢掉；
 * - 图片只允许本站上传的 /api/content/assets/…，外链图片既过不了 CSP 也没必要；
 * - react-markdown 默认的 urlTransform 会过滤 javascript: 等危险协议。
 */

import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components: Components = {
  // 围栏代码块沿用详情页的公式样式
  pre: ({ children }) => <pre className="formula">{children}</pre>,
  table: ({ children }) => (
    <div className="table-wrap">
      <table className="data-table">{children}</table>
    </div>
  ),
  blockquote: ({ children }) => <div className="note">{children}</div>,
  a: ({ href, children }) => {
    const external = !!href && /^https?:/i.test(href);
    return (
      <a
        href={href}
        target={external ? '_blank' : undefined}
        rel={external ? 'noreferrer' : undefined}
      >
        {children}
      </a>
    );
  },
  img: ({ src, alt }) => {
    if (typeof src !== 'string' || !src.startsWith('/api/content/assets/')) return null;
    return <img className="article-img" src={src} alt={alt ?? ''} loading="lazy" />;
  },
};

export function ArticleMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
      {children}
    </ReactMarkdown>
  );
}
