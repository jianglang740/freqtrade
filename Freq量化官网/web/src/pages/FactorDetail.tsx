/*
 * 因子详情：正文由 Markdown 提供（定义 / 公式 / 怎么用 / 限制与坑），
 * 底部那句「因子本身不产生收益」对所有因子都成立，留在页面里。
 */

import { useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { site } from '../content/site';
import { ArticleMarkdown } from '../components/ArticleMarkdown';
import { getFactor } from '../lib/content';
import { useAsync } from '../lib/useAsync';

export function FactorDetail() {
  const { slug } = useParams<{ slug: string }>();
  const { data, loading, error, reload } = useAsync(() => getFactor(slug ?? ''), [slug]);

  const meta = data?.meta;

  useEffect(() => {
    if (!meta) return;
    const prev = document.title;
    document.title = `${meta.name} · ${site.name}`;
    return () => {
      document.title = prev;
    };
  }, [meta]);

  if (loading) {
    return (
      <section className="wrap section">
        <div className="empty">读取中…</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="wrap section">
        <div className="empty">
          {error}
          <div style={{ marginTop: 18 }}>
            <button className="btn" type="button" onClick={reload}>
              重试
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (!meta) {
    return (
      <section className="wrap section">
        <div className="empty">
          没有这个因子。
          <div style={{ marginTop: 18 }}>
            <Link className="btn" to="/factors">
              返回因子库
            </Link>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="wrap">
      <div className="article article--board">
        <Link to="/factors" className="small muted">
          ← 因子库
        </Link>

        <div className="kicker" style={{ marginTop: 20 }}>
          {meta.latin} · {meta.status}
        </div>
        <h1 style={{ marginTop: 12 }}>{meta.name}</h1>
        <p className="lead" style={{ marginTop: 8 }}>
          {meta.oneLine}
        </p>

        <ArticleMarkdown>{data.body}</ArticleMarkdown>

        <p className="note">
          因子本身不产生收益。任何因子都要放进组合、做过成本与样本外检验之后，才有资格叫「有效」。
        </p>
      </div>
    </section>
  );
}
