/*
 * 策略列表：左侧是文字与关键指标，右侧是净值曲线与订阅价格。
 * 卡片整块可点，进入详情页。
 *
 * 内容来自后端 content/strategies/*.md，由 /admin 维护，前端不再硬编码。
 */

import { Link } from 'react-router-dom';
import { site } from '../content/site';
import { EquityCurve } from '../components/EquityCurve';
import { listStrategies } from '../lib/content';
import { useAsync } from '../lib/useAsync';
import { usePageMeta } from '../lib/pageMeta';

export function Strategies() {
  const { data, loading, error, reload } = useAsync(listStrategies, []);
  usePageMeta({
    title: '策略',
    description:
      '经过研究、回测与模拟盘验证的加密量化策略，查看收益率、回撤与净值曲线，按订阅获取。',
  });

  return (
    <section className="wrap section">
      <div className="kicker">FREQ / 策略</div>
      <h1 className="display" style={{ fontSize: 'clamp(30px, 4.4vw, 56px)', marginTop: 14 }}>
        策略
      </h1>
      <p className="lead" style={{ marginTop: 18 }}>
        {/* 一行写完：JSX 里换行会被折成一个空格，中文标点后面会多出一个空格 */}
        {'这里的每一款策略，都经过我们的投研人员反复研究，为了建模我们耗费了大量的精力搭建数据库，所以您不用担心用于训练模型的数据质量问题，同时也不必担心策略的风控能力。'}
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
        <div className="empty">还没有已发布的策略。</div>
      )}

      {!loading && !error && data && data.length > 0 && (
        <div className="card-grid">
          {data.map((s) => (
            <Link key={s.slug} to={`/strategies/${s.slug}`} className="strategy-card">
              <div className="strategy-card__body">
                <div className="metric-label">
                  {s.latin} · <span style={{ whiteSpace: 'nowrap' }}>{s.statusLabel}</span>
                </div>
                <div className="strategy-card__title">{s.name}</div>
                <p className="strategy-card__desc">{s.desc}</p>
                <div className="strategy-card__meta">
                  {s.metrics.map((m) => (
                    <span key={m.label}>
                      {m.label}{' '}
                      <b style={{ color: 'var(--rh-cream)', fontWeight: 500 }}>{m.value}</b>
                    </span>
                  ))}
                </div>
              </div>

              <div className="strategy-card__chart">
                <EquityCurve data={s.curve} label={s.curveNote} />
                <div className="strategy-card__price">
                  {s.status === 'live'
                    ? `获取订阅｜${s.priceLabel ?? site.priceLabel}`
                    : '模拟盘观察中，暂不开放订阅'}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      <p className="small muted" style={{ marginTop: 28, letterSpacing: '0.06em' }}>
        * 标记「开放订阅」的策略可以订阅；标记「模拟盘观察中」的策略正在我们的模拟账户上运行，暂不对外销售。
      </p>
    </section>
  );
}
