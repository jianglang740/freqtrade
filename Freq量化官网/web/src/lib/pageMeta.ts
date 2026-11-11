/*
 * 每页的 title / description / robots。
 *
 * 静态 HTML 里的默认 meta 是给不执行 JS 的抓取方（微信、X 等）看的；
 * 这里的动态更新面向浏览器与会执行 JS 的搜索引擎。
 * 分享图统一用 /og.png，写绝对地址（相对路径多数平台不认）。
 */

import { useEffect } from 'react';

const SITE_NAME = 'Freq量化社区';

export const DEFAULT_TITLE = `${SITE_NAME} · 加密量化交易`;

export const DEFAULT_DESC =
  '用机器和算法解决主观交易困境。基于工业级引擎 freqtrade 运行量化策略，只通过交易所 API 交易，不托管资金。';

function setMeta(attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute('content', content);
}

export function usePageMeta({
  title,
  description = DEFAULT_DESC,
  noindex = false,
}: {
  title?: string;
  description?: string;
  noindex?: boolean;
} = {}) {
  useEffect(() => {
    const fullTitle = title ? `${title} · ${SITE_NAME}` : DEFAULT_TITLE;
    const image = new URL('/og.png', window.location.origin).href;

    document.title = fullTitle;
    setMeta('name', 'description', description);
    setMeta('name', 'robots', noindex ? 'noindex, nofollow' : 'index, follow');
    setMeta('property', 'og:title', fullTitle);
    setMeta('property', 'og:description', description);
    setMeta('property', 'og:image', image);
    setMeta('name', 'twitter:title', fullTitle);
    setMeta('name', 'twitter:description', description);
    setMeta('name', 'twitter:image', image);
  }, [title, description, noindex]);
}
