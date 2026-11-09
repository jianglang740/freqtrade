/*
 * 「我的机器人」——全站唯一的动态页面。
 * 布局分两块：左边是功能栏（功能按钮 + 机器人切换），右边是 FreqHub 风格的面板区
 * （KPI 卡片行 + 数据表）。登录后只显示当前账号名下的机器人（后端 /api/bots 已按归属过滤）。
 * 面板是只读的：下单、启停、改配置一律由服务方执行。
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { usePageMeta } from '../lib/pageMeta';
import {
  botGet,
  createBot,
  getUser,
  listBots,
  logout as apiLogout,
  restoreSession,
  type BalanceSummary,
  type Bot,
  type ClosedTrade,
  type EnterTagPerformance,
  type ExitReasonPerformance,
  type HistoricBalance,
  type MixTagPerformance,
  type OpenTrade,
  type PairPerformance,
  type ProfitSummary,
  type ShowConfig,
  type SiteUser,
  testBotConnection,
} from '../lib/api';
import { LoginPanel } from '../components/LoginPanel';

type Tab = 'overview' | 'equity' | 'stats' | 'positions' | 'closed' | 'order' | 'general' | 'balance';

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'overview', label: '机器人总览', hint: '所有机器人的实时状态' },
  { id: 'equity', label: '净值曲线', hint: '账户净值与回撤：每日快照' },
  { id: 'stats', label: '交易统计', hint: '钱从哪来：按交易对、标签、出场原因拆分' },
  { id: 'positions', label: '持仓', hint: '当前还没平掉的仓位' },
  { id: 'closed', label: '已平仓', hint: '最近 50 笔平仓记录' },
  { id: 'order', label: '下单', hint: '只读指令台：手动下单需要服务方执行' },
  { id: 'general', label: '常规', hint: '这台机器人的运行参数' },
  { id: 'balance', label: '余额', hint: '账户余额与持仓占用' },
];

/* ───────────────────────── 小工具 ───────────────────────── */

function num(n: number | undefined | null, digits = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 带正负号的金额，负数本身带减号 */
function signed(n: number | undefined | null, digits = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return n >= 0 ? `+${num(n, digits)}` : num(n, digits);
}

/** 入参已经是百分数（例如 20.02 表示 +20.02%） */
function pct(n: number | undefined | null, digits = 2) {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function rate(n: number | undefined | null) {
  if (n === undefined || n === null || Number.isNaN(n)) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

/** freqtrade 的状态机是英文，界面上翻一下 */
function stateLabel(state?: string) {
  const s = (state || '').toUpperCase();
  const map: Record<string, string> = {
    RUNNING: '运行中',
    STOPPED: '已停止',
    PAUSED: '已暂停',
    RELOAD_CONFIG: '重载中',
  };
  return map[s] || state || '未知';
}

function tfLabel(tf?: string) {
  if (!tf) return '—';
  return /^\d+$/.test(tf) ? `${tf}m` : tf;
}

/** trade_duration 的单位是分钟 */
function dur(minutes?: number) {
  if (minutes === undefined || minutes === null) return '—';
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} 分`;
  if (m < 1440) return `${Math.floor(m / 60)} 时 ${m % 60} 分`;
  return `${Math.floor(m / 1440)} 天 ${Math.floor((m % 1440) / 60)} 时`;
}

function exitLabel(reason?: string) {
  if (!reason) return '—';
  const map: Record<string, string> = {
    roi: '止盈',
    stop_loss: '止损',
    trailing_stop_loss: '移动止损',
    exit_signal: '信号出场',
    // force_exit 是服务方这边平掉的仓，不是爆仓。别用「强制平仓」（币圈=爆仓），
    // 也别用「人工平仓」——会让客户以为有人在账户里手动下单，所以取最中性的「系统出场」
    force_exit: '系统出场',
    custom_exit: '自定义出场',
    emergency_exit: '紧急出场',
    liquidation: '爆仓',
  };
  return map[reason] || reason;
}

function port(url?: string) {
  try {
    return new URL(url || '').port;
  } catch {
    return '';
  }
}

function clock() {
  return new Date().toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/* ───────────────────────── 通用块 ───────────────────────── */

function Kpi({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'up' | 'down';
}) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className={`kpi__value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {sub && <div className="kpi__sub">{sub}</div>}
    </div>
  );
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {sub && <p className="panel__sub">{sub}</p>}
      </header>
      {children}
    </section>
  );
}

/** 窄屏时表格允许横向滚动，不把整页撑破 */
function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="table-wrap">
      <table className="table">{children}</table>
    </div>
  );
}

/** 读一个只读接口，tick 变化时重取 */
function useBotGet<T>(botId: string, path: string, tick: number) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setErr(null);
    botGet<T>(botId, path)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : '读取失败');
      });
    return () => {
      alive = false;
    };
  }, [botId, path, tick]);

  return { data, err };
}

/* ───────────────────────── 添加机器人 ───────────────────────── */

function AddBotModal({
  canWrite,
  onClose,
  onCreated,
}: {
  canWrite: boolean;
  onClose: () => void;
  onCreated: (bot: Bot) => void;
}) {
  const [name, setName] = useState('');
  const [apiUrl, setApiUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  /** 测试连接的结果；改了输入就清掉，免得拿旧结果误导 */
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    setTestResult(null);
  }, [apiUrl, username, password]);

  const test = async () => {
    if (!apiUrl.trim() || !username.trim() || !password) {
      setTestResult({ ok: false, text: '先填好 API 地址、用户名和密码' });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      await testBotConnection({
        apiUrl: apiUrl.trim().replace(/\/+$/, ''),
        username: username.trim(),
        password,
      });
      setTestResult({ ok: true, text: '连接成功：这台机器人可以加进列表' });
    } catch (err) {
      setTestResult({ ok: false, text: err instanceof Error ? err.message : '连接失败' });
    } finally {
      setTesting(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const bot = await createBot({
        name: name.trim(),
        // 后端要求 API 地址不带路径，顺手去掉结尾的斜杠
        apiUrl: apiUrl.trim().replace(/\/+$/, ''),
        username: username.trim(),
        password,
      });
      onCreated(bot);
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-bot-title"
      onClick={onClose}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
        <h3 id="add-bot-title">添加机器人</h3>
        <p>填入这台 freqtrade 的 REST 地址和账号。可以先点「测试连接」验一下；提交时还会再试连一次，连不上不会加进列表。</p>

        <form className="add-bot-form" onSubmit={submit}>
          <label>
            <span className="metric-label">名称</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如 网格 1h"
              required
            />
          </label>

          <label>
            <span className="metric-label">API 地址</span>
            <input
              className="input"
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="http://127.0.0.1:8080"
              required
            />
          </label>

          <div className="add-bot-row">
            <label>
              <span className="metric-label">用户名</span>
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <label>
              <span className="metric-label">密码</span>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
              />
            </label>
          </div>

          {error && <p className="form-error">{error}</p>}
          {testResult && (
            <p className={testResult.ok ? 'form-ok' : 'form-error'}>{testResult.text}</p>
          )}

          {!canWrite && (
            <p className="form-hint">
              当前账号是只读账号。如果提交后提示没有写权限，请联系服务方把这台机器人挂到你的账号下。
            </p>
          )}

          <div className="form-actions">
            <button
              type="button"
              className="btn btn--sm"
              onClick={test}
              disabled={testing || busy}
            >
              {testing ? '测试中…' : '测试连接'}
            </button>
            <span className="form-actions__gap" />
            <button type="button" className="btn btn--sm" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn btn--primary btn--sm" disabled={busy || testing}>
              {busy ? '连接中…' : '连接并添加'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ───────────────────────── 机器人总览 ───────────────────────── */

type Snapshot = { cfg?: ShowConfig; profit?: ProfitSummary; err?: string };

function Overview({
  bots,
  tick,
  onOpen,
}: {
  bots: Bot[];
  tick: number;
  onOpen: (id: string, tab: Tab) => void;
}) {
  const [rows, setRows] = useState<Record<string, Snapshot>>({});

  useEffect(() => {
    let alive = true;
    Promise.all(
      bots.map(async (b) => {
        try {
          const [cfg, profit] = await Promise.all([
            botGet<ShowConfig>(b.id, 'api/v1/show_config'),
            botGet<ProfitSummary>(b.id, 'api/v1/profit'),
          ]);
          return [b.id, { cfg, profit }] as const;
        } catch (e) {
          return [b.id, { err: e instanceof Error ? e.message : '机器人离线或连不上' }] as const;
        }
      })
    ).then((entries) => {
      if (alive) setRows(Object.fromEntries(entries));
    });
    return () => {
      alive = false;
    };
  }, [bots, tick]);

  const snapshots = bots.map((b) => rows[b.id]);
  const loaded = snapshots.some(Boolean);
  const running = snapshots.filter((r) => (r?.cfg?.state || '').toUpperCase() === 'RUNNING').length;
  const closed = snapshots.reduce((s, r) => s + (r?.profit?.closed_trade_count ?? 0), 0);
  const pnl = snapshots.reduce((s, r) => s + (r?.profit?.profit_all_coin ?? 0), 0);

  return (
    <>
      <div className="kpi-row">
        <Kpi label="机器人数" value={String(bots.length)} sub="当前账号名下" />
        <Kpi label="运行中" value={loaded ? String(running) : '—'} sub={`共 ${bots.length} 台`} />
        <Kpi label="已平仓笔数" value={loaded ? String(closed) : '—'} sub="全部机器人合计" />
        <Kpi
          label="总盈亏"
          value={loaded ? signed(pnl) : '—'}
          sub="已平仓合计：USDT"
          tone={pnl >= 0 ? 'up' : 'down'}
        />
      </div>

      <Panel title="机器人状态" sub="点一行进入这台机器人的持仓">
        {!loaded ? (
          <div className="empty">读取中…</div>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>机器人</th>
                <th>策略</th>
                <th>周期</th>
                <th>模式</th>
                <th style={{ textAlign: 'right' }}>已平仓</th>
                <th style={{ textAlign: 'right' }}>总盈亏</th>
                <th style={{ textAlign: 'right' }}>胜率</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {bots.map((b) => {
                const r = rows[b.id];
                const profit = r?.profit;
                const runningRow = (r?.cfg?.state || '').toUpperCase() === 'RUNNING';
                return (
                  <tr
                    key={b.id}
                    className="row-link"
                    onClick={() => onOpen(b.id, 'positions')}
                    title="查看持仓"
                  >
                    <td>{b.name}</td>
                    <td className="muted">{r?.cfg?.strategy || '—'}</td>
                    <td className="muted">{tfLabel(r?.cfg?.timeframe)}</td>
                    <td className="muted">{r?.cfg?.dry_run === undefined ? '—' : r.cfg.dry_run ? '模拟盘' : '实盘'}</td>
                    <td style={{ textAlign: 'right' }}>{profit?.closed_trade_count ?? '—'}</td>
                    <td
                      style={{ textAlign: 'right' }}
                      className={`${(profit?.profit_all_coin ?? 0) >= 0 ? 'up' : 'down'} tabular`}
                    >
                      {profit ? signed(profit.profit_all_coin) : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }} className="tabular">
                      {profit ? rate(profit.winrate) : '—'}
                    </td>
                    <td>
                      {r?.err ? (
                        <span className="status status--bad">
                          <i className="dot dot--off" />
                          离线
                        </span>
                      ) : !b.isEnabled ? (
                        <span className="status">
                          <i className="dot dot--off" />
                          已停用
                        </span>
                      ) : (
                        <span className="status">
                          <i className={`dot${runningRow ? '' : ' dot--off'}`} />
                          {stateLabel(r?.cfg?.state)}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

/* ───────────────────────── 持仓 ───────────────────────── */

function Positions({ botId, tick }: { botId: string; tick: number }) {
  const { data: trades, err } = useBotGet<OpenTrade[]>(botId, 'api/v1/status', tick);

  if (err) return <div className="empty">{err}</div>;
  if (!trades) return <div className="empty">读取中…</div>;

  const profit = trades.reduce((s, t) => s + (t.profit_abs ?? 0), 0);
  const stake = trades.reduce((s, t) => s + (t.stake_amount ?? 0), 0);

  return (
    <>
      <div className="kpi-row kpi-row--3">
        <Kpi label="持仓数" value={String(trades.length)} sub="当前未平仓" />
        <Kpi
          label="浮动盈亏"
          value={signed(profit)}
          sub="按现价估算：USDT"
          tone={profit >= 0 ? 'up' : 'down'}
        />
        <Kpi label="占用保证金" value={num(stake)} sub="各仓位投入合计：USDT" />
      </div>

      <Panel title="当前持仓" sub={`共 ${trades.length} 笔：每 15 秒刷新`}>
        {trades.length === 0 ? (
          <div className="empty">当前没有持仓。</div>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>交易对</th>
                <th>开仓时间</th>
                <th style={{ textAlign: 'right' }}>开仓价</th>
                <th style={{ textAlign: 'right' }}>当前价</th>
                <th style={{ textAlign: 'right' }}>数量</th>
                <th style={{ textAlign: 'right' }}>盈亏</th>
                <th style={{ textAlign: 'right' }}>盈亏率</th>
                <th style={{ textAlign: 'right' }}>时长</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const abs = t.profit_abs ?? 0;
                return (
                  <tr key={t.trade_id}>
                    <td>{t.pair}</td>
                    <td className="muted">{t.open_date}</td>
                    <td style={{ textAlign: 'right' }} className="tabular">
                      {num(t.open_rate, 4)}
                    </td>
                    <td style={{ textAlign: 'right' }} className="tabular">
                      {num(t.current_rate, 4)}
                    </td>
                    <td style={{ textAlign: 'right' }} className="tabular">
                      {num(t.amount, 6)}
                    </td>
                    <td style={{ textAlign: 'right' }} className={`${abs >= 0 ? 'up' : 'down'} tabular`}>
                      {signed(abs)}
                    </td>
                    <td style={{ textAlign: 'right' }} className={`${abs >= 0 ? 'up' : 'down'} tabular`}>
                      {pct((t.profit_ratio ?? 0) * 100)}
                    </td>
                    <td style={{ textAlign: 'right' }} className="muted">
                      {dur(t.trade_duration)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

/* ───────────────────────── 已平仓 ───────────────────────── */

function Closed({ botId, tick }: { botId: string; tick: number }) {
  const { data: profit } = useBotGet<ProfitSummary>(botId, 'api/v1/profit', tick);
  const { data, err } = useBotGet<{ trades: ClosedTrade[] } | ClosedTrade[]>(
    botId,
    // order_by_id=false → 最近成交优先
    'api/v1/trades?limit=50&order_by_id=false',
    tick
  );

  if (err) return <div className="empty">{err}</div>;
  if (!data) return <div className="empty">读取中…</div>;

  const trades = Array.isArray(data) ? data : data.trades || [];
  const all = profit?.profit_all_coin ?? 0;

  return (
    <>
      <div className="kpi-row">
        <Kpi
          label="累计盈亏"
          value={signed(profit?.profit_all_coin)}
          sub="已平仓合计：USDT"
          tone={all >= 0 ? 'up' : 'down'}
        />
        <Kpi
          label="收益率"
          value={pct(profit?.profit_all_percent)}
          sub="相对起始资金"
          tone={(profit?.profit_all_percent ?? 0) >= 0 ? 'up' : 'down'}
        />
        <Kpi label="胜率" value={rate(profit?.winrate)} sub="盈利笔数占比" />
        <Kpi label="盈亏比" value={num(profit?.profit_factor)} sub="Profit factor：总盈利 / 总亏损" />
      </div>

      <Panel title="最近平仓" sub={`最近 ${trades.length} 笔：按平仓时间倒序`}>
        {trades.length === 0 ? (
          <div className="empty">还没有平仓记录。</div>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>交易对</th>
                <th>开仓</th>
                <th>平仓</th>
                <th style={{ textAlign: 'right' }}>平仓价</th>
                <th style={{ textAlign: 'right' }}>持仓</th>
                <th style={{ textAlign: 'right' }}>盈亏</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.trade_id}>
                  <td>{t.pair}</td>
                  <td className="muted">{t.open_date}</td>
                  <td className="muted">{t.close_date}</td>
                  <td style={{ textAlign: 'right' }} className="tabular">
                    {num(t.close_rate, 4)}
                  </td>
                  <td style={{ textAlign: 'right' }} className="muted">
                    {dur(t.trade_duration)}
                  </td>
                  <td style={{ textAlign: 'right' }} className={`${t.profit_abs >= 0 ? 'up' : 'down'} tabular`}>
                    {signed(t.profit_abs)}
                    <span className="muted">（{pct(t.profit_ratio * 100)}）</span>
                  </td>
                  <td className="muted small">{exitLabel(t.exit_reason)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

/* ───────────────────────── 净值曲线 ───────────────────────── */

type EquityPoint = { ts: number; date: string; value: number };

const EQUITY_RANGES = [
  { id: 'all', label: '全部' },
  { id: '365', label: '1 年' },
  { id: '90', label: '90 天' },
  { id: '30', label: '30 天' },
] as const;
type EquityRange = (typeof EQUITY_RANGES)[number]['id'];

/** historic_balance 返回的是「列名 + 二维数组」，这里按列名映射成绘图层要的结构 */
function useEquityPoints(botId: string, tick: number) {
  const { data, err } = useBotGet<HistoricBalance>(botId, 'api/v1/historic_balance', tick);

  const points = useMemo<EquityPoint[]>(() => {
    if (!data || !Array.isArray(data.columns) || !Array.isArray(data.data)) return [];
    const iDate = data.columns.indexOf('date');
    const iTs = data.columns.indexOf('__date_ts');
    const iVal = data.columns.indexOf('total_quote');
    if (iDate < 0 || iVal < 0) return [];
    return data.data
      .map((row) => {
        const rawDate = String(row[iDate] ?? '');
        const ts = iTs >= 0 ? Number(row[iTs]) : Date.parse(rawDate);
        return { ts, date: rawDate.slice(0, 10), value: Number(row[iVal]) };
      })
      .filter((p) => Number.isFinite(p.ts) && Number.isFinite(p.value))
      .sort((a, b) => a.ts - b.ts);
  }, [data]);

  return { points, err };
}

/** 坐标轴上的金额，尽量短：12,265 → 12.3k */
function axisNum(v: number) {
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  return v.toFixed(1);
}

/**
 * 纯 SVG 折线图：网格 + 面积 + 折线 + hover 十字线。
 * 宽度用 ResizeObserver 实测，避免 viewBox 拉伸把坐标文字拉变形。
 */
function LineChart({
  data,
  height = 260,
  stroke,
  baseline,
  axisFormat,
  tipFormat,
  clampTop = false,
}: {
  data: EquityPoint[];
  height?: number;
  stroke: string;
  baseline?: number;
  axisFormat: (v: number) => string;
  tipFormat: (p: EquityPoint) => string;
  /** 回撤图的顶边就是 0%，不要再往上留白，否则会出现 +0.2% 这种刻度 */
  clampTop?: boolean;
}) {
  const plotRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(760);
  const [hover, setHover] = useState<number | null>(null);
  const uid = useId().replace(/:/g, '');

  useEffect(() => {
    const el = plotRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 数据点多的时候按步长抽稀，SVG 不至于塞几千段线；hover 跟着抽稀后的点走
  const stride = Math.max(1, Math.ceil(data.length / 900));
  const drawn = useMemo(
    () => data.filter((_, i) => i % stride === 0 || i === data.length - 1),
    [data, stride]
  );

  const padL = 62;
  const padR = 12;
  const padT = 12;
  const padB = 24;
  const W = Math.max(300, width);
  const H = height;
  const iw = W - padL - padR;
  const ih = H - padT - padB;

  let lo = Math.min(...drawn.map((p) => p.value));
  let hi = Math.max(...drawn.map((p) => p.value));
  if (baseline !== undefined) {
    lo = Math.min(lo, baseline);
    hi = Math.max(hi, baseline);
  }
  if (hi - lo < 1e-9) {
    const pad = Math.abs(hi) * 0.01 || 1;
    lo -= pad;
    hi += pad;
  }
  const padY = (hi - lo) * 0.06;
  lo -= padY;
  if (!clampTop) hi += padY;

  const x = (i: number) => padL + (drawn.length <= 1 ? iw / 2 : (i / (drawn.length - 1)) * iw);
  const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * ih;

  const line = drawn.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
  const baseY = baseline !== undefined ? y(baseline) : y(lo);
  const area = `${line} L${x(drawn.length - 1).toFixed(2)},${baseY.toFixed(2)} L${x(0).toFixed(2)},${baseY.toFixed(2)} Z`;

  // 横轴标签：宽屏最多 5 个，窄屏减到 3 个，否则 390px 下会叠字
  const spanDays = (drawn[drawn.length - 1].ts - drawn[0].ts) / 86400000;
  const dateLabel = (iso: string) => (spanDays > 300 ? iso.slice(0, 7) : iso.slice(5));
  const ratios = W < 560 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];
  const tickIdx = [...new Set(ratios.map((r) => Math.round(r * (drawn.length - 1))))];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const ratio = Math.min(1, Math.max(0, (px - padL) / iw));
    setHover(Math.round(ratio * (drawn.length - 1)));
  };

  const tip = hover !== null ? drawn[hover] : null;
  const tipX = hover !== null ? Math.min(Math.max(x(hover), 84), W - 84) : 0;

  return (
    <div className="chart-box">
      <div className="chart-plot" ref={plotRef}>
        <svg
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          role="img"
        >
          <defs>
            <linearGradient id={`fill-${uid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.3" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {[0, 0.25, 0.5, 0.75, 1].map((r) => {
            const v = hi - (hi - lo) * r;
            return (
              <g key={r}>
                <line
                  x1={padL}
                  x2={W - padR}
                  y1={y(v)}
                  y2={y(v)}
                  stroke="#33205c"
                  strokeWidth={0.6}
                  strokeDasharray="2 3"
                />
                <text x={padL - 8} y={y(v) + 3.5} textAnchor="end" fill="#8f7bb8" fontSize={10}>
                  {axisFormat(v)}
                </text>
              </g>
            );
          })}

          {baseline !== undefined && (
            <line
              x1={padL}
              x2={W - padR}
              y1={y(baseline)}
              y2={y(baseline)}
              stroke="#4b2f7e"
              strokeWidth={0.8}
              strokeDasharray="4 3"
            />
          )}

          <path d={area} fill={`url(#fill-${uid})`} />
          <path d={line} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />

          {tickIdx.map((i) => (
            <text
              key={i}
              x={x(i)}
              y={H - 6}
              textAnchor={i === 0 ? 'start' : i === drawn.length - 1 ? 'end' : 'middle'}
              fill="#8f7bb8"
              fontSize={10}
            >
              {dateLabel(drawn[i].date)}
            </text>
          ))}

          <circle cx={x(drawn.length - 1)} cy={y(drawn[drawn.length - 1].value)} r={2.4} fill={stroke} />

          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke={stroke} strokeWidth={0.8} strokeDasharray="3 3" />
              <circle cx={x(hover)} cy={y(drawn[hover].value)} r={3} fill={stroke} stroke="#120a24" strokeWidth={1.5} />
            </g>
          )}
        </svg>

        {tip && (
          <div className="chart-tip" style={{ left: tipX, top: padT + 6 }}>
            <span className="muted">{tip.date}</span>
            <br />
            <strong>{tipFormat(tip)}</strong>
          </div>
        )}
      </div>
    </div>
  );
}

function Equity({ botId, tick }: { botId: string; tick: number }) {
  const { points, err } = useEquityPoints(botId, tick);
  // 钱包快照一天只有一条。再用 /balance 的总资产补一个「实时」点，
  // 免得客户当天打开页面时曲线一直停在昨天。
  const { data: bal } = useBotGet<BalanceSummary>(botId, 'api/v1/balance', tick);
  const [range, setRange] = useState<EquityRange>('all');

  const { series, live } = useMemo(() => {
    if (points.length === 0) return { series: points, live: false };
    const total = bal?.total ?? bal?.value;
    if (typeof total !== 'number' || !Number.isFinite(total) || total <= 0) {
      return { series: points, live: false };
    }
    const today = new Date().toISOString().slice(0, 10);
    const last = points[points.length - 1];
    if (last.date === today) {
      // 当天已有快照，用实时余额盖掉，避免同一天出现两个点
      return { series: [...points.slice(0, -1), { ...last, value: total }], live: true };
    }
    return { series: [...points, { ts: Date.now(), date: today, value: total }], live: true };
  }, [points, bal]);

  const stats = useMemo(() => {
    if (series.length < 2) return null;
    const first = series[0];
    const last = series[series.length - 1];
    let peak = first.value;
    let maxDD = 0;
    let trough = first.value;
    let ddDate = first.date;
    for (const p of series) {
      if (p.value > peak) peak = p.value;
      const dd = p.value / peak - 1;
      if (dd < maxDD) {
        maxDD = dd;
        trough = p.value;
        ddDate = p.date;
      }
    }
    return {
      first,
      last,
      ret: (last.value / first.value - 1) * 100,
      maxDD: maxDD * 100,
      trough,
      ddDate,
    };
  }, [series]);

  // 回撤只在当前区间内算（峰值从区间起点重新计），跟图表范围一致
  const drawdown = useMemo(() => {
    const cut =
      range === 'all' || series.length === 0
        ? series
        : series.slice(Math.max(0, series.findIndex((p) => p.ts >= series[series.length - 1].ts - Number(range) * 86400000)));
    let peak = -Infinity;
    return cut.map((p) => {
      peak = Math.max(peak, p.value);
      return { ts: p.ts, date: p.date, value: p.value / peak - 1 };
    });
  }, [series, range]);

  const sliced = useMemo(() => {
    if (range === 'all' || series.length === 0) return series;
    const from = series[series.length - 1].ts - Number(range) * 86400000;
    const i = series.findIndex((p) => p.ts >= from);
    return i <= 0 ? series : series.slice(i);
  }, [series, range]);

  if (err) return <div className="empty">{err}</div>;
  if (!stats) return <div className="empty">这台机器人还没有足够的净值快照。</div>;

  return (
    <>
      <div className="kpi-row kpi-row--3">
        <Kpi
          label="最新净值"
          value={num(stats.last.value)}
          sub={`${live ? '实时' : '快照'} ${stats.last.date}：起始 ${num(stats.first.value)}`}
        />
        <Kpi
          label="累计收益"
          value={pct(stats.ret)}
          sub={`${stats.first.date} → ${stats.last.date}`}
          tone={stats.ret >= 0 ? 'up' : 'down'}
        />
        <Kpi
          label="最大回撤"
          value={pct(stats.maxDD)}
          sub={`谷底 ${num(stats.trough)}：${stats.ddDate}`}
          tone="down"
        />
      </div>

      <Panel title="净值曲线" sub={`每日快照${live ? ' + 最新实时点' : ''}：USDT，共 ${sliced.length} 天`}>
        <div className="chart-tools">
          {EQUITY_RANGES.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`chart-range${range === r.id ? ' is-active' : ''}`}
              onClick={() => setRange(r.id)}
            >
              {r.label}
            </button>
          ))}
        </div>
        <LineChart
          data={sliced}
          stroke="#f9c74f"
          baseline={sliced[0]?.value}
          axisFormat={axisNum}
          tipFormat={(p) => num(p.value)}
        />
      </Panel>

      <Panel title="回撤" sub="从区间内峰值回落">
        <LineChart
          data={drawdown}
          height={150}
          stroke="#f47b5c"
          baseline={0}
          clampTop
          axisFormat={(v) => `${(v * 100).toFixed(1)}%`}
          tipFormat={(p) => `${(p.value * 100).toFixed(2)}%`}
        />
      </Panel>
    </>
  );
}

/* ───────────────────────── 交易统计 ───────────────────────── */

/** 盈亏贡献条：宽度按该组绝对值占最大组的比例 */
function ProfitBar({ value, max }: { value: number; max: number }) {
  const width = Math.min(100, Math.max(2, (Math.abs(value) / max) * 100));
  return (
    <span className={`profit-bar${value >= 0 ? '' : ' profit-bar--down'}`}>
      <i style={{ width: `${width}%` }} />
    </span>
  );
}

function StatTable<T extends { profit_abs: number; profit_pct: number; count: number }>({
  rows,
  head,
  render,
}: {
  rows: T[];
  head: string;
  render: (r: T) => React.ReactNode;
}) {
  const max = Math.max(...rows.map((r) => Math.abs(r.profit_abs)), 1);
  return (
    <Table>
      <thead>
        <tr>
          <th>{head}</th>
          <th style={{ textAlign: 'right' }}>笔数</th>
          <th style={{ textAlign: 'right' }}>盈亏</th>
          <th style={{ textAlign: 'right' }}>投入回报率</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td>{render(r)}</td>
            <td style={{ textAlign: 'right' }} className="muted tabular">
              {r.count}
            </td>
            <td style={{ textAlign: 'right' }} className="tabular">
              <span className={r.profit_abs >= 0 ? 'up' : 'down'}>{signed(r.profit_abs)}</span>
              <ProfitBar value={r.profit_abs} max={max} />
            </td>
            <td
              style={{ textAlign: 'right' }}
              className={`tabular ${r.profit_pct >= 0 ? 'up' : 'down'}`}
            >
              {pct(r.profit_pct)}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

/** mix_tag 的格式是「enter_tag + 空格 + exit_reason」，从最后一个空格拆 */
function splitMixTag(mix: string) {
  const i = mix.lastIndexOf(' ');
  if (i < 0) return { tag: mix, reason: '' };
  return { tag: mix.slice(0, i), reason: mix.slice(i + 1) };
}

function TradeStats({ botId, tick }: { botId: string; tick: number }) {
  const { data: pairs, err } = useBotGet<PairPerformance[]>(botId, 'api/v1/performance', tick);
  const { data: tags } = useBotGet<EnterTagPerformance[]>(botId, 'api/v1/entries', tick);
  const { data: exits } = useBotGet<ExitReasonPerformance[]>(botId, 'api/v1/exits', tick);
  const { data: mixes } = useBotGet<MixTagPerformance[]>(botId, 'api/v1/mix_tags', tick);

  if (err) return <div className="empty">{err}</div>;
  if (!pairs) return <div className="empty">读取中…</div>;

  const total = pairs.reduce((s, p) => s + p.profit_abs, 0);
  const count = pairs.reduce((s, p) => s + p.count, 0);
  const wins = pairs.filter((p) => p.profit_abs > 0).length;
  const top = [...pairs].sort((a, b) => b.profit_abs - a.profit_abs)[0];
  const share = top && total !== 0 ? (top.profit_abs / total) * 100 : 0;

  const byPair = [...pairs].sort((a, b) => b.profit_abs - a.profit_abs);
  const byTag = [...(tags ?? [])].sort((a, b) => b.profit_abs - a.profit_abs);
  const byExit = [...(exits ?? [])].sort((a, b) => b.profit_abs - a.profit_abs);
  const byMix = [...(mixes ?? [])].sort((a, b) => b.profit_abs - a.profit_abs);

  return (
    <>
      <div className="kpi-row kpi-row--3">
        <Kpi
          label="交易总盈亏"
          value={signed(total)}
          sub={`合计 ${count} 笔`}
          tone={total >= 0 ? 'up' : 'down'}
        />
        <Kpi label="盈利交易对" value={`${wins} / ${pairs.length}`} sub="按已平仓盈亏" />
        <Kpi
          label="最大贡献"
          value={top ? top.pair.split('/')[0] : '—'}
          sub={top ? `${signed(top.profit_abs)}：占 ${share.toFixed(0)}%` : '—'}
          tone={top && top.profit_abs >= 0 ? 'up' : 'down'}
        />
      </div>

      <Panel title="按交易对" sub="按累计盈亏排序：投入回报率 = 累计盈亏 ÷ 累计开仓金额">
        {byPair.length === 0 ? (
          <div className="empty">还没有平仓记录。</div>
        ) : (
          <StatTable rows={byPair} head="交易对" render={(r) => r.pair} />
        )}
      </Panel>

      <Panel title="按入场标签" sub="策略给出的 enter_tag 各自贡献多少">
        {byTag.length === 0 ? (
          <div className="empty">还没有带标签的平仓记录。</div>
        ) : (
          <StatTable rows={byTag} head="入场标签" render={(r) => r.enter_tag || '未标记'} />
        )}
      </Panel>

      <Panel title="按出场原因" sub="止盈、止损、移动止损分别贡献多少">
        {byExit.length === 0 ? (
          <div className="empty">还没有平仓记录。</div>
        ) : (
          <StatTable rows={byExit} head="出场原因" render={(r) => exitLabel(r.exit_reason)} />
        )}
      </Panel>

      <Panel title="标签 × 出场" sub="入场标签与出场原因的组合：按盈亏排序">
        {byMix.length === 0 ? (
          <div className="empty">还没有组合明细。</div>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>入场标签</th>
                <th>出场原因</th>
                <th style={{ textAlign: 'right' }}>笔数</th>
                <th style={{ textAlign: 'right' }}>盈亏</th>
              </tr>
            </thead>
            <tbody>
              {byMix.map((m) => {
                const { tag, reason } = splitMixTag(m.mix_tag);
                return (
                  <tr key={m.mix_tag}>
                    <td>{tag}</td>
                    <td className="muted small">{exitLabel(reason)}</td>
                    <td style={{ textAlign: 'right' }} className="muted tabular">
                      {m.count}
                    </td>
                    <td
                      style={{ textAlign: 'right' }}
                      className={`tabular ${m.profit_abs >= 0 ? 'up' : 'down'}`}
                    >
                      {signed(m.profit_abs)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>
    </>
  );
}

/* ───────────────────────── 下单：只读指令台 ───────────────────────── */

const READONLY_CMDS = [
  '/profit',
  '/balance',
  '/status',
  '/show_config',
  '/daily',
  '/trades',
  '/performance',
  '/version',
];

function ConsoleBox({ botId }: { botId: string }) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ cmd: string; body: string; ok: boolean }[]>([]);

  const run = async (raw: string) => {
    const cmd = raw.trim().replace(/^\//, '').split(/\s+/)[0];
    if (!cmd) return;
    const full = `/${cmd}`;
    if (!READONLY_CMDS.includes(full)) {
      setOut((prev) =>
        [{ cmd: `/${cmd}`, body: `只支持只读指令：${READONLY_CMDS.join('  ')}`, ok: false }, ...prev].slice(0, 6)
      );
      return;
    }
    setBusy(true);
    try {
      const suffix = full === '/trades' ? '?limit=10&order_by_id=false' : '';
      const data = await botGet<unknown>(botId, `api/v1/${full.slice(1)}${suffix}`);
      setOut((prev) => [{ cmd: full, body: JSON.stringify(data, null, 2), ok: true }, ...prev].slice(0, 6));
    } catch (e) {
      setOut((prev) =>
        [{ cmd: full, body: e instanceof Error ? e.message : '请求失败', ok: false }, ...prev].slice(0, 6)
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="指令控制台"
      sub="只读：这里只发 GET 请求，不会开仓、平仓或改配置。手动下单请联系服务方。"
    >
      <div className="console-pad">
        <div className="console-quick">
          {READONLY_CMDS.map((c) => (
            <button key={c} type="button" onClick={() => run(c)} disabled={busy}>
              {c}
            </button>
          ))}
        </div>

        <form
          className="console-bar"
          onSubmit={(e) => {
            e.preventDefault();
            run(input);
            setInput('');
          }}
        >
          <input
            className="input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入指令，例如 /profit、/balance"
            aria-label="只读指令"
          />
          <button className="btn btn--sm" type="submit" disabled={busy}>
            {busy ? '执行中…' : '执行'}
          </button>
        </form>

        {out.length > 0 && (
          <div className="console-out">
            {out.map((o, i) => (
              <div key={`${o.cmd}-${i}`} className={`console-entry${o.ok ? '' : ' is-err'}`}>
                <div className="console-entry__cmd">$ {o.cmd}</div>
                <pre>{o.body}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}

/* ───────────────────────── 常规 ───────────────────────── */

function General({ botId, tick }: { botId: string; tick: number }) {
  const { data: cfg, err } = useBotGet<ShowConfig>(botId, 'api/v1/show_config', tick);

  if (err) return <div className="empty">{err}</div>;
  if (!cfg) return <div className="empty">读取中…</div>;

  const mode =
    cfg.trading_mode === 'futures' ? '合约' : cfg.trading_mode === 'spot' ? '现货' : cfg.trading_mode || '—';
  const margin =
    cfg.margin_mode === 'cross' ? '全仓' : cfg.margin_mode === 'isolated' ? '逐仓' : cfg.margin_mode || '—';

  const rows: [string, string][] = [
    ['策略', cfg.strategy || '—'],
    ['状态机', stateLabel(cfg.state)],
    ['运行模式', cfg.dry_run === undefined ? '—' : cfg.dry_run ? '模拟盘（dry-run）' : '实盘'],
    ['交易所', cfg.exchange || '—'],
    ['交易市场', mode],
    ['保证金模式', margin],
    ['K 线周期', tfLabel(cfg.timeframe)],
    ['计价币', cfg.stake_currency || '—'],
    [
      '每笔投入',
      cfg.stake_amount === undefined || cfg.stake_amount === null
        ? '—'
        : `${cfg.stake_amount} ${cfg.stake_currency || ''}`.trim(),
    ],
    ['最大同时持仓', cfg.max_open_trades === undefined ? '—' : String(cfg.max_open_trades)],
    // freqtrade 的 stoploss 是比率：-0.25 就是 -25%
    ['止损', typeof cfg.stoploss === 'number' ? `${(cfg.stoploss * 100).toFixed(0)}%` : '—'],
    ['加仓', cfg.position_adjustment_enable === undefined ? '—' : cfg.position_adjustment_enable ? '开启' : '关闭'],
  ];

  return (
    <Panel title="运行参数" sub="来自机器人 /api/v1/show_config 的实时配置">
      <div className="kv-grid">
        {rows.map(([k, v]) => (
          <div className="kv" key={k}>
            <span className="kv__k">{k}</span>
            <span className="kv__v">{v}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ───────────────────────── 余额 ───────────────────────── */

function Balance({ botId, tick }: { botId: string; tick: number }) {
  const { data: bal, err } = useBotGet<BalanceSummary>(botId, 'api/v1/balance', tick);

  if (err) return <div className="empty">{err}</div>;
  if (!bal) return <div className="empty">读取中…</div>;

  const free =
    bal.currencies?.filter((c) => !c.is_position).reduce((s, c) => s + (c.free ?? 0), 0) ?? 0;
  const note = bal.note === 'Simulated balances' ? '模拟盘余额' : bal.note;

  return (
    <>
      <div className="kpi-row">
        <Kpi label="总资产" value={num(bal.total)} sub={`${note || '余额'}：${bal.stake || '—'}`} />
        <Kpi label="起始资金" value={num(bal.starting_capital)} sub={`初始本金：${bal.stake || '—'}`} />
        <Kpi label="可用" value={num(free)} sub={`未占用部分：${bal.stake || '—'}`} />
        <Kpi
          label="收益率"
          value={pct(bal.starting_capital_pct)}
          sub="相对起始资金"
          tone={(bal.starting_capital_pct ?? 0) >= 0 ? 'up' : 'down'}
        />
      </div>

      <Panel title="币种明细" sub="freetrade 报告的是可用 / 占用 / 合计">
        {bal.currencies && bal.currencies.length > 0 ? (
          <Table>
            <thead>
              <tr>
                <th>币种</th>
                <th style={{ textAlign: 'right' }}>可用</th>
                <th style={{ textAlign: 'right' }}>占用</th>
                <th style={{ textAlign: 'right' }}>合计</th>
                <th style={{ textAlign: 'right' }}>估值</th>
              </tr>
            </thead>
            <tbody>
              {bal.currencies.map((c) => (
                <tr key={c.currency}>
                  <td>
                    {c.currency}
                    {c.is_position && <span className="muted small">（持仓）</span>}
                  </td>
                  <td style={{ textAlign: 'right' }} className="tabular">
                    {num(c.free, 8)}
                  </td>
                  <td style={{ textAlign: 'right' }} className="tabular">
                    {num(c.used, 8)}
                  </td>
                  <td style={{ textAlign: 'right' }} className="tabular">
                    {num(c.total ?? c.balance, 8)}
                  </td>
                  <td style={{ textAlign: 'right' }} className="tabular">
                    {num(c.est_stake, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <div className="empty">没有可用余额明细。</div>
        )}
      </Panel>
    </>
  );
}

/* ───────────────────────── 控制台外壳 ───────────────────────── */

export function Robots() {
  // 登录后的仪表盘，不该被搜索引擎收录
  usePageMeta({
    title: '我的机器人',
    description: '登录后查看自己名下机器人的运行状态、持仓与交易记录。',
    noindex: true,
  });
  const [user, setUser] = useState<SiteUser | null>(() => getUser());
  const [bots, setBots] = useState<Bot[] | null>(null);
  const [botsErr, setBotsErr] = useState<string | null>(null);
  const [activeBot, setActiveBot] = useState<string>('');
  const [addOpen, setAddOpen] = useState(false);
  // 用 URL 保存当前标签，刷新和分享链接都能回到同一个位置
  const [params, setParams] = useSearchParams();
  const rawTab = params.get('tab');
  const tab: Tab = TABS.some((t) => t.id === rawTab) ? (rawTab as Tab) : 'overview';
  const setTab = (next: Tab) => {
    setParams(next === 'overview' ? {} : { tab: next }, { replace: true });
  };

  // 每 15 秒自动刷新一次，右上角的「刷新」按钮可以手动触发
  const [tick, setTick] = useState(0);
  const [stamp, setStamp] = useState(() => clock());
  const bump = useCallback(() => {
    setTick((t) => t + 1);
    setStamp(clock());
  }, []);

  // 页面在后台时暂停轮询：客户开着标签页去干别的，不该继续占用服务端。
  // 切回来立刻刷新一次，保证看到的是最新数据。
  useEffect(() => {
    let id: number | undefined;
    const start = () => {
      if (id !== undefined) return;
      id = window.setInterval(bump, 15000);
    };
    const stop = () => {
      if (id === undefined) return;
      window.clearInterval(id);
      id = undefined;
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        bump();
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [bump]);

  const loadBots = useCallback(() => {
    setBotsErr(null);
    listBots()
      .then((list) => {
        setBots(list);
        if (list.length > 0) setActiveBot((cur) => cur || list[0].id);
      })
      .catch((e) => {
        setBotsErr(e instanceof Error ? e.message : '读取机器人列表失败');
        setBots([]);
      });
  }, []);

  // 挂载时用 Cookie 向后端确认会话；本地缓存的 user 只用于首屏
  useEffect(() => {
    let alive = true;
    restoreSession().then((next) => {
      if (!alive) return;
      setUser(next);
      if (!next) {
        setBots(null);
        setActiveBot('');
        setAddOpen(false);
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  // 任意请求遇到 401 时 api.ts 会清缓存并广播事件，这里同步回登录面板
  useEffect(() => {
    const onSession = () => {
      if (!getUser()) {
        setUser(null);
        setBots(null);
        setActiveBot('');
        setAddOpen(false);
      }
    };
    window.addEventListener('freq-session', onSession);
    return () => window.removeEventListener('freq-session', onSession);
  }, []);

  useEffect(() => {
    if (user) loadBots();
  }, [user, loadBots]);

  const selected = useMemo(() => bots?.find((b) => b.id === activeBot) ?? bots?.[0], [bots, activeBot]);

  const logout = async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setBots(null);
      setActiveBot('');
      setAddOpen(false);
    }
  };

  const handleCreated = (bot: Bot) => {
    setAddOpen(false);
    setActiveBot(bot.id);
    loadBots();
    setTab('overview');
  };

  if (!user) return <LoginPanel onDone={setUser} />;

  const tabMeta = TABS.find((t) => t.id === tab)!;
  const subtitle =
    tab === 'overview' || !selected ? tabMeta.hint : `${tabMeta.hint}（${selected.name}）`;

  let content: React.ReactNode;
  if (botsErr) {
    content = <div className="empty">{botsErr}</div>;
  } else if (bots === null) {
    content = <div className="empty">读取中…</div>;
  } else if (bots.length === 0) {
    content = (
      <div className="empty">这个账号下面还没有分配机器人。联系服务方把机器人挂到你的账号上。</div>
    );
  } else if (tab === 'overview') {
    content = <Overview bots={bots} tick={tick} onOpen={(id, next) => {
      setActiveBot(id);
      setTab(next);
    }} />;
  } else if (!selected) {
    content = <div className="empty">找不到这台机器人。</div>;
  } else {
    const key = selected.id;
    content = (
      <>
        {tab === 'equity' && <Equity key={key} botId={key} tick={tick} />}
        {tab === 'stats' && <TradeStats key={key} botId={key} tick={tick} />}
        {tab === 'positions' && <Positions key={key} botId={key} tick={tick} />}
        {tab === 'closed' && <Closed key={key} botId={key} tick={tick} />}
        {tab === 'order' && <ConsoleBox key={key} botId={key} />}
        {tab === 'general' && <General key={key} botId={key} tick={tick} />}
        {tab === 'balance' && <Balance key={key} botId={key} tick={tick} />}
      </>
    );
  }

  return (
    <section className="wrap console">
      <aside className="sidebar">
        <div className="side-brand">
          <span className="side-brand__mark">＋</span>
          <span>
            FREQ<span className="side-brand__dim"> / 面板</span>
          </span>
        </div>

        <nav className="side-nav">
          {TABS.map((t, i) => (
            <button
              key={t.id}
              type="button"
              className={`side-btn${tab === t.id ? ' is-active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              <span className="side-btn__idx">{String(i + 1).padStart(2, '0')}</span>
              <span>{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="side-label-row">
            <span className="side-label">机器人</span>
            <button
              type="button"
              className="side-add"
              onClick={() => setAddOpen(true)}
              title="添加机器人"
              aria-label="添加机器人"
            >
              ＋ 添加
            </button>
          </div>
          {botsErr ? (
            <div className="side-user">列表读取失败</div>
          ) : bots === null ? (
            <div className="side-user">读取中…</div>
          ) : bots.length === 0 ? (
            <div className="side-user">暂无机器人</div>
          ) : (
            bots.map((b) => (
              <button
                key={b.id}
                type="button"
                className={`side-btn side-bot${selected?.id === b.id ? ' is-active' : ''}`}
                onClick={() => setActiveBot(b.id)}
                title={b.name}
              >
                <i className={`dot${b.isEnabled ? '' : ' dot--off'}`} />
                <span className="side-bot__name">{b.name}</span>
                <span className="side-bot__port">{port(b.apiUrl)}</span>
              </button>
            ))
          )}

          <div className="side-user side-login">
            已登录：{user.username}
          </div>
          <button type="button" className="btn btn--sm side-exit" onClick={logout}>
            退出登录
          </button>
        </div>
      </aside>

      <div className="console-main">
        <div className="hub-head">
          <div>
            <h1 className="hub-head__title">{tabMeta.label}</h1>
            <p className="hub-head__sub">{subtitle}</p>
          </div>
          <div className="hub-head__tools">
            <span className="hub-stamp">更新于 {stamp}</span>
            <button type="button" className="hub-refresh" onClick={bump}>
              ↻ 刷新
            </button>
          </div>
        </div>

        {content}
      </div>

      {addOpen && (
        <AddBotModal
          canWrite={user.role === 'superadmin'}
          onClose={() => setAddOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </section>
  );
}
