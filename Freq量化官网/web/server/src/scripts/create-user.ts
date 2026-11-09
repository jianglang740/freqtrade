import { hashPassword } from '../services/auth.js';
import { getUserByUsername, insertUser } from '../services/users.js';
import { audit } from '../services/audit.js';
import { fail, optStr, parseArgs, readPassword, str } from './lib.js';

async function main(): Promise<void> {
  const flags = parseArgs(process.argv.slice(2));

  const username = str(flags, 'username');
  if (!/^[A-Za-z0-9_.@-]{1,64}$/.test(username)) {
    fail('--username 只能包含字母、数字、_ . @ -，长度 1-64');
  }

  const role = optStr(flags, 'role') ?? 'user';
  if (role !== 'user' && role !== 'superadmin') {
    fail('--role 只能是 user 或 superadmin');
  }

  if (getUserByUsername(username)) {
    fail(`用户名已存在：${username}`);
  }

  const password = readPassword(flags);
  if (password.length < 8) fail('密码至少 8 位');
  if (password.length > 128) fail('密码最多 128 位');

  const passwordHash = await hashPassword(password);
  const user = insertUser({
    username,
    name: optStr(flags, 'name') ?? null,
    email: optStr(flags, 'email') ?? null,
    password_hash: passwordHash,
    role,
  });

  audit({ actorId: null, action: 'user_created_by_cli', target: user.id, detail: username });

  console.log(`已创建用户 ${user.username}（id=${user.id}, role=${user.role}）`);
}

main().catch((err: unknown) => {
  console.error('[error]', err instanceof Error ? err.message : 'unknown error');
  process.exit(1);
});
