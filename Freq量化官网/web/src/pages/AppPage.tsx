/*
 * App 页：FreqDroid 简介。静态内容。
 */

import { useContact } from '../components/ContactModal';

const FEATURES = [
  {
    title: '行情与自选',
    body: '主流行情聚合到一处，自选列表本地保存，不需要注册账号。',
  },
  {
    title: '纸面交易',
    body: '用真实行情做模拟下单，先把手感练出来，再拿真金白银。',
  },
  {
    title: '策略跟踪',
    body: '把线上机器人挂到手机上，随时看一眼持仓和当日盈亏。',
  },
  {
    title: '离线演示',
    body: '内置一套完整的演示数据，断网也能把功能点一遍。',
  },
];

export function AppPage() {
  const contact = useContact();

  return (
    <section className="wrap section">
      <div className="kicker">FREQ / App</div>
      <h1 className="display" style={{ fontSize: 'clamp(28px, 4.2vw, 54px)', marginTop: 14 }}>
        FreqDroid
      </h1>
      <p className="lead" style={{ marginTop: 18 }}>
        安卓端的量化工具箱：看行情、记自选、做纸面交易，把线上机器人的状态装进口袋。
      </p>

      <div className="grid-2" style={{ marginTop: 36 }}>
        <div className="card">
          <div className="metric-label">状态</div>
          <div className="metric-value" style={{ margin: '8px 0 14px' }}>
            内测中
          </div>
          <p className="strategy-card__desc" style={{ margin: 0 }}>
            目前只以安装包形式在内部流通，不上架应用商店。想要试用可以找我拿包。
          </p>
          <button className="btn btn--primary" style={{ marginTop: 20 }} onClick={contact.open}>
            获取安装包
          </button>
        </div>

        <div className="card">
          <div className="metric-label">平台</div>
          <div className="metric-value" style={{ margin: '8px 0 14px' }}>
            Android
          </div>
          <p className="strategy-card__desc" style={{ margin: 0 }}>
            只做安卓。iOS 版本暂时没有计划——没有绕过 App Store 的合规做法，而我们的体量上不了架。
          </p>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 22 }}>
        {FEATURES.map((f) => (
          <div className="card" key={f.title}>
            <div className="strategy-card__title" style={{ marginBottom: 8 }}>
              {f.title}
            </div>
            <p className="strategy-card__desc" style={{ margin: 0 }}>
              {f.body}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
