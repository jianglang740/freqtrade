/*
 * 因子库列表：纯静态内容，不涉及订阅。
 * 内容来自后端 content/factors/*.md，由 /admin 维护。
 */

import { Link } from 'react-router-dom';
import { listFactors } from '../lib/content';
import { useAsync } from '../lib/useAsync';

export function Factors() {
  const { data, loading, error, reload } = useAsync(listFactors, []);

  return (
    <section className="wrap section">
      <div className="kicker">FREQ / 因子库</div>
      <h1 className="display" style={{ fontSize: 'clamp(30px, 4.4vw, 56px)', marginTop: 14 }}>
        因子库
      </h1>
      <p className="lead" style={{ marginTop: 18 }}>
        {/* 一行写完：JSX 里换行会被折成一个空格，中文标点后面会多出一个空格 */}
        {'这里分享一些我们团队产出的择时因子或横截面选币因子，供大家参考学习，其中部分因子已用于策略实现，如果您对哪个因子感兴趣或抱有疑问，欢迎与我们取得联系，共同探讨。'}
      </p>

      {loading && <div className="empty">读取中…</div>}

      {error && !loading && (
        <div className="empty">
          {error}
          <div style={{ marginTop: 18 }}>
            <button className="btn" type="button" onClick={reload}>
              重试
            </button>
          </div>
        </div>
      )}

      {!loading && !error && data && data.length === 0 && (
        <div className="empty">还没有已发布的因子。</div>
      )}

      {!loading && !error && data && data.length > 0 && (
        <div className="card-grid">
          {data.map((f) => (
            <Link
              key={f.slug}
              to={`/factors/${f.slug}`}
              className="card factor-card"
              style={{ display: 'block' }}
            >
              <div className="metric-label">
                {f.latin} · {f.status}
              </div>
              <div className="strategy-card__title" style={{ margin: '10px 0 8px' }}>
                {f.name}
              </div>
              <p className="strategy-card__desc" style={{ margin: 0 }}>
                {f.oneLine}
              </p>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
