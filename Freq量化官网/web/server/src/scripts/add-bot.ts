import { createBot, getBotByName } from '../services/bots.js';
import { testBotConnection } from '../services/freqtrade.js';
import { assertBotApiUrl } from '../services/urlSecurity.js';
import { audit } from '../services/audit.js';
import { fail, optStr, parseArgs, readPassword, str } from './lib.js';

async function main(): Promise<void> {
  const flags = parseArgs(process.argv.slice(2));

  const name = str(flags, 'name');
  const rawApiUrl = str(flags, 'api-url');
  const botUsername = str(flags, 'username');

  if (getBotByName(name)) fail(`机器人名称已存在：${name}`);

  let apiUrl: string;
  try {
    apiUrl = assertBotApiUrl(rawApiUrl);
  } catch (err) {
    fail(err instanceof Error ? err.message : 'API 地址不合法');
  }

  const password = readPassword(flags);

  console.log(`正在测试连接 ${apiUrl} ...`);
  const ok = await testBotConnection(apiUrl, botUsername, password);
  if (!ok) fail('连接失败：请检查 API 地址、用户名与密码（机器人需已启动）');

  const bot = createBot({
    name,
    apiUrl,
    username: botUsername,
    password,
    notes: optStr(flags, 'notes') ?? null,
  });

  audit({ actorId: null, action: 'bot_created_by_cli', target: bot.id, detail: bot.name });

  console.log(`已登记机器人 ${bot.name}（id=${bot.id}, api=${bot.api_url}）`);
}

main().catch((err: unknown) => {
  console.error('[error]', err instanceof Error ? err.message : 'unknown error');
  process.exit(1);
});
