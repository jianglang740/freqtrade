/**
 * 压测用的假 freqtrade：只实现前端会读的 GET 接口。
 * 目的：在不打扰真实的 8080 / 8082 dry-run 实例的前提下，
 * 给官网后端制造 50 台机器人量级的上游流量。
 *
 *   MOCK_PORT=18080 MOCK_DELAY_MS=8 npx tsx src/scripts/loadtest/mock-freqtrade.ts
 */
import http from 'node:http';

const PORT = Number(process.env.MOCK_PORT || 18080);
const DELAY_MS = Number(process.env.MOCK_DELAY_MS || 8);
const TOKEN = 'mock-access-token';

const openTrades = [1, 2].map((i) => ({
  trade_id: i,
  pair: `BTC/USDT${i > 1 ? `-${i}` : ''}`,
  amount: 0.01 * i,
  open_rate: 62000 + i * 100,
  current_rate: 62500,
  open_date: '2026-11-01 08:00:00',
  stake_amount: 620,
  profit_abs: 3.2 * i,
  profit_ratio: 0.005,
  trade_duration: 300,
  is_open: true,
  enter_tag: 'grid',
}));

const closedTrades = Array.from({ length: 50 }, (_, i) => ({
  trade_id: 1000 + i,
  pair: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'][i % 3],
  open_date: '2026-10-01 00:00:00',
  close_date: '2026-10-01 04:00:00',
  close_rate: 62000,
  profit_abs: (i % 7) - 3,
  profit_ratio: ((i % 7) - 3) / 1000,
  trade_duration: 240,
  exit_reason: 'roi',
  is_open: false,
}));

const rows = (key: string, n: number) =>
  Array.from({ length: n }, (_, i) => ({
    [key]: `${key}-${i}`,
    profit_ratio: 0.01,
    profit_pct: 1,
    profit_abs: 10,
    count: 5,
  }));

const historicBalance = {
  columns: ['date', '__date_ts', 'total_quote'],
  length: 365,
  data: Array.from({ length: 365 }, (_, i) => [
    `2025-01-${String((i % 28) + 1).padStart(2, '0')}`,
    1700000000 + i * 86400,
    1000 + i,
  ]),
};

const payloads: Record<string, unknown> = {
  'api/v1/ping': { status: 'pong' },
  'api/v1/status': openTrades,
  'api/v1/profit': {
    profit_all_percent: 2.5,
    profit_all_coin: 125.4,
    closed_trade_count: 50,
    trade_count: 60,
    winning_trades: 30,
    losing_trades: 20,
    winrate: 0.6,
    profit_factor: 1.2,
    avg_duration: '4:00:00',
  },
  'api/v1/balance': {
    stake: 'USDT',
    total: 1000,
    value: 1000,
    currencies: [
      { currency: 'USDT', free: 500, used: 500, balance: 1000, est_stake: 1000 },
      { currency: 'BTC', free: 0.01, used: 0, balance: 0.01, est_stake: 625 },
    ],
  },
  'api/v1/trades': closedTrades,
  'api/v1/show_config': {
    state: 'RUNNING',
    dry_run: true,
    strategy: 'GridMartin',
    exchange: 'binance',
    trading_mode: 'spot',
    stake_currency: 'USDT',
    stake_amount: 100,
    max_open_trades: 3,
    timeframe: '1h',
    stoploss: -0.25,
  },
  'api/v1/performance': rows('pair', 20),
  'api/v1/entries': rows('enter_tag', 20),
  'api/v1/exits': rows('exit_reason', 20),
  'api/v1/mix_tags': rows('mix_tag', 20),
  'api/v1/historic_balance': historicBalance,
};

const counts = new Map<string, number>();

const server = http.createServer((req, res) => {
  const url = (req.url || '/').split('?')[0]!.replace(/^\/+/, '');
  counts.set(url, (counts.get(url) ?? 0) + 1);

  if (req.method === 'POST' && url === 'api/v1/token/login') {
    const auth = req.headers.authorization || '';
    if (!auth.startsWith('Basic ')) {
      res.writeHead(401).end('{"error":"unauthorized"}');
      return;
    }
    setTimeout(() => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ access_token: TOKEN }));
    }, DELAY_MS);
    return;
  }

  if (req.method !== 'GET') {
    res.writeHead(405).end('{"error":"method not allowed"}');
    return;
  }
  if (req.headers.authorization !== `Bearer ${TOKEN}`) {
    res.writeHead(401).end('{"error":"unauthorized"}');
    return;
  }

  const payload = payloads[url];
  if (payload === undefined) {
    res.writeHead(404).end('{"error":"not found"}');
    return;
  }

  setTimeout(() => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(payload));
  }, DELAY_MS);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[mock] freqtrade listening on http://127.0.0.1:${PORT} delay=${DELAY_MS}ms`);
});

process.on('SIGINT', () => {
  console.log(`[mock] requests: ${JSON.stringify(Object.fromEntries(counts))}`);
  server.close(() => process.exit(0));
});
