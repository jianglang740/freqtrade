import { getBotById, getBotByName, unassignBot } from '../services/bots.js';
import { getUserByUsername } from '../services/users.js';
import { audit } from '../services/audit.js';
import { fail, parseArgs, str } from './lib.js';

function main(): void {
  const flags = parseArgs(process.argv.slice(2));

  const username = str(flags, 'username');
  const botRef = str(flags, 'bot');

  const user = getUserByUsername(username);
  if (!user) fail(`找不到用户：${username}`);

  const bot = getBotById(botRef) ?? getBotByName(botRef);
  if (!bot) fail(`找不到机器人（用 id 或名称）：${botRef}`);

  if (!unassignBot(bot.id, user.id)) {
    fail(`${user.username} 本来就没有绑定 ${bot.name}`);
  }

  audit({
    actorId: null,
    action: 'bot_unassigned_by_cli',
    target: bot.id,
    detail: `${bot.name} ✕ ${user.username}`,
  });

  console.log(`已解绑：${user.username} ✕ ${bot.name}`);
}

main();
