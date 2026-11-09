/**
 * 压测数据：N 个客户，每人一台指向 mock freqtrade 的机器人。
 * 只写 loadtest 库，使用前请带 DATABASE_PATH=./data/loadtest.db。
 *
 *   DATABASE_PATH=./data/loadtest.db npx tsx src/scripts/loadtest/seed.ts --users 50
 */
import { run, get } from '../../db/index.js';
import { hashPassword } from '../../services/auth.js';
import { insertUser } from '../../services/users.js';
import { assignBot, createBot } from '../../services/bots.js';
import { fail, parseArgs } from '../lib.js';

const MOCK_URL = process.env.MOCK_URL || 'http://127.0.0.1:18080';
const PASSWORD = 'loadtest-Passw0rd!';

function clean(): void {
  run(`DELETE FROM bot_ownership WHERE bot_id IN (SELECT id FROM bots WHERE name LIKE 'lt\\_bot\\_%' ESCAPE '\\')`);
  run(`DELETE FROM audit_logs WHERE target IN (SELECT id FROM users WHERE username LIKE 'lt\\_user\\_%' ESCAPE '\\')`);
  run(`DELETE FROM users WHERE username LIKE 'lt\\_user\\_%' ESCAPE '\\'`);
  run(`DELETE FROM bots WHERE name LIKE 'lt\\_bot\\_%' ESCAPE '\\'`);
}

async function main(): Promise<void> {
  const flags = parseArgs(process.argv.slice(2));
  const count = Number(flags['users'] ?? 50);
  if (!Number.isInteger(count) || count < 1 || count > 500) fail('--users 需要 1-500 的整数');

  clean();

  const passwordHash = await hashPassword(PASSWORD);

  for (let i = 1; i <= count; i += 1) {
    const suffix = String(i).padStart(2, '0');
    const user = insertUser({
      username: `lt_user_${suffix}`,
      password_hash: passwordHash,
      role: 'user',
    });
    const bot = createBot({
      name: `lt_bot_${suffix}`,
      apiUrl: MOCK_URL,
      username: 'lt',
      password: 'lt-bot-password',
    });
    assignBot({ botId: bot.id, userId: user.id, createdBy: user.id });
  }

  const users = (get(`SELECT count(*) AS n FROM users WHERE username LIKE 'lt\\_user\\_%' ESCAPE '\\'`) as unknown as {
    n: number;
  }).n;
  const bots = (get(`SELECT count(*) AS n FROM bots WHERE name LIKE 'lt\\_bot\\_%' ESCAPE '\\'`) as unknown as {
    n: number;
  }).n;

  console.log(`已准备压测数据：users=${users} bots=${bots}（mock=${MOCK_URL}）`);
}

main().catch((err: unknown) => {
  console.error('[error]', err instanceof Error ? err.message : err);
  process.exit(1);
});
