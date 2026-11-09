/**
 * 50 客户量级压测。
 *
 *   # 终端 A：假 freqtrade
 *   MOCK_PORT=18080 npx tsx src/scripts/loadtest/mock-freqtrade.ts
 *   # 终端 B：压测后端（独立库，别打生产库）
 *   DATABASE_PATH=./data/loadtest.db PORT=3102 npx tsx src/index.ts
 *   # 终端 C：造数 + 压测
 *   DATABASE_PATH=./data/loadtest.db npx tsx src/scripts/loadtest/seed.ts --users 50
 *   DATABASE_PATH=./data/loadtest.db npx tsx src/scripts/loadtest/run.ts --port 3102 --users 50
 *
 * 会话直接用签名 JWT 构造 Cookie，避免 50 个人同时登录被 IP 限流干扰；
 * 登录风暴单独测。默认每个虚拟用户绑定一个 127.0.0.x 源地址，模拟真实客户各用各的 IP；
 * 加 --same-ip 则所有请求都从 127.0.0.1 发出，用来检查 NAT 场景。
 */
import http from 'node:http';
import { execSync } from 'node:child_process';
import { all } from '../../db/index.js';
import { signSession } from '../../services/auth.js';
import { parseArgs } from '../lib.js';

type Row = { uid: string; bid: string; username: string };

const TAB_PROFILES: { weight: number; paths: string[] }[] = [
  { weight: 40, paths: ['api/v1/show_config', 'api/v1/profit'] }, // 总览
  { weight: 20, paths: ['api/v1/status'] }, // 持仓
  { weight: 15, paths: ['api/v1/profit', 'api/v1/trades?limit=50&order_by_id=false'] }, // 已平仓
  { weight: 10, paths: ['api/v1/performance', 'api/v1/entries', 'api/v1/exits', 'api/v1/mix_tags'] }, // 统计
  { weight: 10, paths: ['api/v1/historic_balance', 'api/v1/balance'] }, // 净值
  { weight: 3, paths: ['api/v1/show_config'] }, // 常规
  { weight: 2, paths: ['api/v1/balance'] }, // 余额
];

const PASSWORD = 'loadtest-Passw0rd!';
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

type Metrics = {
  latencies: number[];
  statuses: Map<number, number>;
  bytes: number;
};

const metrics: Metrics = { latencies: [], statuses: new Map(), bytes: 0 };

function record(status: number, ms: number, bytes: number): void {
  metrics.latencies.push(ms);
  metrics.statuses.set(status, (metrics.statuses.get(status) ?? 0) + 1);
  metrics.bytes += bytes;
}

type Client = {
  agent: http.Agent;
  cookie: string;
  username: string;
  botId: string;
};

function makeClient(row: Row, index: number, sameIp: boolean): Client {
  return {
    agent: new http.Agent({
      keepAlive: true,
      maxSockets: 4,
      ...(sameIp ? {} : { localAddress: `127.0.0.${index + 2}` }),
    }),
    cookie: `freq_session=${signSession(row.uid)}`,
    username: row.username,
    botId: row.bid,
  };
}

function request(
  client: Client,
  port: number,
  path: string,
  init: { method?: string; body?: string } = {},
): Promise<{ status: number; ms: number; bytes: number }> {
  return new Promise((resolve) => {
    const started = Date.now();
    const body = init.body;
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path,
        method: init.method ?? 'GET',
        agent: client.agent,
        headers: {
          Cookie: client.cookie,
          Origin: 'http://127.0.0.1:4000',
          ...(body
            ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }
            : {}),
        },
      },
      (res) => {
        let bytes = 0;
        res.on('data', (chunk: Buffer) => {
          bytes += chunk.length;
        });
        res.on('end', () => resolve({ status: res.statusCode ?? 0, ms: Date.now() - started, bytes }));
      },
    );
    req.on('error', () => resolve({ status: 0, ms: Date.now() - started, bytes: 0 }));
    req.setTimeout(30_000, () => req.destroy(new Error('timeout')));
    if (body) req.write(body);
    req.end();
  });
}

function pickProfile(): string[] {
  const total = TAB_PROFILES.reduce((s, p) => s + p.weight, 0);
  let r = Math.random() * total;
  for (const p of TAB_PROFILES) {
    r -= p.weight;
    if (r <= 0) return p.paths;
  }
  return TAB_PROFILES[0]!.paths;
}

async function fireProfile(client: Client, port: number): Promise<void> {
  const paths = pickProfile();
  await Promise.all(
    paths.map((p) => request(client, port, `/api/bots/${client.botId}/proxy/${p}`).then((r) => record(r.status, r.ms, r.bytes))),
  );
}

async function login(client: Client, port: number): Promise<void> {
  const body = JSON.stringify({ username: client.username, password: PASSWORD });
  const r = await request(client, port, '/api/auth/login', { method: 'POST', body });
  record(r.status, r.ms, r.bytes);
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx]!;
}

function summarize(label: string, elapsedMs: number, cpu: string[]): void {
  const sorted = [...metrics.latencies].sort((a, b) => a - b);
  const total = sorted.length;
  const errors = [...metrics.statuses.entries()]
    .filter(([s]) => s < 200 || s >= 300)
    .reduce((sum, [, n]) => sum + n, 0);
  const p50 = percentile(sorted, 50);
  const p95 = percentile(sorted, 95);
  const p99 = percentile(sorted, 99);
  const max = sorted[total - 1] ?? 0;
  const rps = elapsedMs > 0 ? (total / elapsedMs) * 1000 : 0;

  console.log(`\n=== ${label} ===`);
  console.log(
    `请求 ${total}，错误 ${errors}（${total ? ((errors / total) * 100).toFixed(1) : '0'}%），` +
      `耗时 ${(elapsedMs / 1000).toFixed(1)}s，${rps.toFixed(1)} req/s`,
  );
  console.log(`p50 ${p50}ms / p95 ${p95}ms / p99 ${p99}ms / max ${max}ms`);
  console.log(`状态分布：${JSON.stringify(Object.fromEntries(metrics.statuses))}`);
  console.log(`传输：${(metrics.bytes / 1024 / 1024).toFixed(1)} MiB`);
  if (cpu.length) console.log(`服务端 CPU/RSS 采样：${cpu.join('  ')}`);

  // 重置，供下一个阶段使用
  metrics.latencies = [];
  metrics.statuses = new Map();
  metrics.bytes = 0;
}

async function main(): Promise<void> {
  const flags = parseArgs(process.argv.slice(2));
  const port = Number(flags['port'] ?? 3102);
  const limit = Number(flags['users'] ?? 50);
  const durationSec = Number(flags['duration'] ?? 60);
  const sameIp = Boolean(flags['same-ip']);
  const phase = String(flags['phase'] ?? 'all');
  const pid = flags['pid'] ? Number(flags['pid']) : null;

  const rows = all(
    `SELECT u.id AS uid, u.username, o.bot_id AS bid
       FROM users u
       JOIN bot_ownership o ON o.user_id = u.id
      WHERE u.username LIKE 'lt\\_user\\_%' ESCAPE '\\'
      ORDER BY u.username
      LIMIT ?`,
    limit,
  ) as unknown as Row[];

  if (rows.length < limit) {
    console.error(`[error] 只有 ${rows.length} 个压测用户，先运行 seed.ts`);
    process.exit(1);
  }

  const clients = rows.map((row, i) => makeClient(row, i, sameIp));
  console.log(
    `压测：${clients.length} 客户，目标 127.0.0.1:${port}，` +
      `${sameIp ? '同一 IP（NAT）' : '每户独立 IP'}，阶段=${phase}`,
  );

  // 每 2 秒采一次服务端 CPU / 内存
  const cpuSamples: string[] = [];
  let sampler: NodeJS.Timeout | null = null;
  if (pid) {
    sampler = setInterval(() => {
      try {
        const out = execSync(`ps -o %cpu=,rss= -p ${pid}`, { encoding: 'utf8' }).trim();
        const [cpu, rss] = out.split(/\s+/);
        cpuSamples.push(`${cpu}%/${(Number(rss) / 1024).toFixed(0)}MiB`);
        if (cpuSamples.length > 10) cpuSamples.shift();
      } catch {
        /* 进程已退出 */
      }
    }, 2000);
  }

  if (phase === 'all' || phase === 'steady') {
    const started = Date.now();
    await Promise.all(
      clients.map(async (client) => {
        const end = started + durationSec * 1000;
        while (Date.now() < end) {
          await sleep(Math.random() * 15_000);
          if (Date.now() >= end) break;
          await fireProfile(client, port);
        }
      }),
    );
    summarize(`稳态：${clients.length} 客户 × ${durationSec}s（15s 轮询）`, Date.now() - started, cpuSamples);
  }

  if (phase === 'all' || phase === 'burst') {
    const rounds = 10;
    const started = Date.now();
    for (let round = 0; round < rounds; round += 1) {
      await Promise.all(clients.map((client) => fireProfile(client, port)));
      await sleep(3000);
    }
    summarize(`突发：${rounds} 轮 × ${clients.length} 客户同时刷新`, Date.now() - started, cpuSamples);
  }

  if (phase === 'all' || phase === 'login') {
    const started = Date.now();
    await Promise.all(clients.map((client) => login(client, port)));
    summarize(`登录风暴：${clients.length} 人同时登录`, Date.now() - started, cpuSamples);
  }

  if (sampler) clearInterval(sampler);

  // 诚实交代压测机的身份：这些结果来自本机回环，不含真实公网 RTT
  console.log('\n[note] 本机回环压测，不含公网 RTT；真实客户延迟还要加网络往返。');
}

main().catch((err: unknown) => {
  console.error('[error]', err instanceof Error ? err.message : err);
  process.exit(1);
});
