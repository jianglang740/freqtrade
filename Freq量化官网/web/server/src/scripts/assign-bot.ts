import { assignBot, getBotById, getBotByName } from '../services/bots.js';
import { getUserByUsername } from '../services/users.js';
import { audit } from '../services/audit.js';
import { fail, optStr, parseArgs, parseExpiry, str } from './lib.js';

function main(): void {
  const flags = parseArgs(process.argv.slice(2));

  const username = str(flags, 'username');
  const botRef = str(flags, 'bot');

  const user = getUserByUsername(username);
  if (!user) fail(`找不到用户：${username}`);

  const bot = getBotById(botRef) ?? getBotByName(botRef);
  if (!bot) fail(`找不到机器人（用 id 或名称）：${botRef}`);

  const expiresRaw = optStr(flags, 'expires');
  const expiresAt = expiresRaw ? parseExpiry(expiresRaw) : null;

  assignBot({
    botId: bot.id,
    userId: user.id,
    createdBy: user.id,
    expiresAt,
  });

  audit({
    actorId: null,
    action: 'bot_assigned_by_cli',
    target: bot.id,
    detail: `${bot.name} → ${user.username}`,
  });

  const expiryText = expiresAt ? new Date(expiresAt).toISOString() : '永久';
  console.log(`已绑定：${user.username} ← ${bot.name}（到期：${expiryText}）`);
}

main();
