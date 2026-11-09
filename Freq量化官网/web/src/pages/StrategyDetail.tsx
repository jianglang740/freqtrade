/*
 * 策略详情：顶部指标 + 曲线，中间 Markdown 正文，底部订阅按钮。
 * slug 对不上时给出回到列表的出口。
 */

import { useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { site } from '../content/site';
import { EquityCurve } from '../components/EquityCurve';
import { ArticleMarkdown } from '../components/ArticleMarkdown';
import { useContact } from '../components/ContactModal';
import { getStrategy } from '../lib/content';
import { useAsync } from '../lib/useAsync';

export function StrategyDetail() {
  const { slug } = useParams<{ slug: string }>();
  const contact = useContact();
  const { data, loading, error, reload } = useAsync(
    () => getStrategy(slug ?? ''),
    [slug],
  );

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
          没有这个策略。
          <div style={{ marginTop: 18 }}>
            <Link className="btn" to="/strategies">
              返回策略列表
            </Link>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="wrap">
      <div className="article article--board">
        <Link to="/strategies" className="small muted">
          ← 策略
        </Link>

        <div className="kicker" style={{ marginTop: 20 }}>
          {meta.latin} · {meta.statusLabel}
        </div>
        <h1 style={{ marginTop: 12 }}>{meta.name}</h1>
        <p className="lead" style={{ marginTop: 8 }}>
          {meta.tagline}
        </p>

        <div className="detail-top">
          <div className="card">
            <EquityCurve data={meta.curve} label={meta.curveNote} />
            <p className="small muted" style={{ margin: '10px 0 0', letterSpacing: '0.06em' }}>
              {meta.curveNote}
            </p>
          </div>

          <div className="card card--accent" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div className="stat-row stat-row--3" style={{ margin: 0 }}>
              {meta.metrics.map((m) => (
                <div className="stat" key={m.label}>
                  <div className="metric-label">{m.label}</div>
                  <div className="metric-value">{m.value}</div>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 'auto' }}>
              <div className="metric-label">订阅价格</div>
              <div className="metric-value" style={{ color: 'var(--rh-gold)', fontSize: 30 }}>
                {meta.status === 'live' ? site.priceLabel : '暂不开放'}
              </div>
              <button
                className="btn btn--primary"
                style={{ width: '100%', justifyContent: 'center', marginTop: 12 }}
                onClick={contact.open}
                disabled={meta.status !== 'live'}
              >
                {meta.status === 'live' ? `获取订阅｜${site.priceLabel}` : '模拟盘观察中'}
              </button>
            </div>
          </div>
        </div>

        <h2>策略说明</h2>
        <ArticleMarkdown>{data.body}</ArticleMarkdown>

        {meta.disclaimer && <div className="note">{meta.disclaimer}</div>}

        <div style={{ marginTop: 40, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <button
            className="btn btn--primary"
            onClick={contact.open}
            disabled={meta.status !== 'live'}
          >
            {meta.status === 'live' ? `获取订阅｜${site.priceLabel}` : '暂不开放订阅'}
          </button>
          <Link className="btn" to="/strategies">
            看看其他策略
          </Link>
        </div>
      </div>
    </section>
  );
}
