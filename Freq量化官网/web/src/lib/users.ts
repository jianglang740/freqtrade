/*
 * 用户管理接口。只有 superadmin 能调用，后端会在每次请求时重新校验角色。
 */

import { request } from './api';

export type AdminUser = {
  id: string;
  username: string;
  name: string | null;
  email: string | null;
  role: 'user' | 'superadmin';
  isActive: boolean;
  locked: boolean;
  botCount: number;
  lastLogin: number | null;
  createdAt: number;
};

export async function listUsers(): Promise<AdminUser[]> {
  const res = await request<{ users: AdminUser[] }>('/users');
  return res.users;
}

export async function createUser(input: {
  username: string;
  password?: string;
  name?: string;
  email?: string;
  role?: 'user' | 'superadmin';
}): Promise<{ user: AdminUser; generatedPassword?: string }> {
  return request('/users', { method: 'POST', body: JSON.stringify(input) });
}

export async function updateUser(
  id: string,
  input: { name?: string; email?: string; isActive?: boolean },
): Promise<AdminUser> {
  const res = await request<{ user: AdminUser }>(`/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
  return res.user;
}

export async function resetUserPassword(
  id: string,
  password?: string,
): Promise<{ generatedPassword?: string }> {
  return request(`/users/${encodeURIComponent(id)}/password`, {
    method: 'POST',
    body: JSON.stringify({ password: password ?? '' }),
  });
}

export async function deleteUser(id: string): Promise<void> {
  await request(`/users/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** 导出按钮直接用这个地址，浏览器会带上同源 Cookie */
export const USERS_EXPORT_URL = '/api/users/export';

/* ───────── 机器人归属（同为 superadmin 接口） ───────── */

export type BotOwner = {
  userId: string;
  username: string;
  name: string | null;
  expiresAt: number | null;
  createdAt: number;
};

export type AdminBot = {
  id: string;
  name: string;
  apiUrl: string;
  username: string;
  isEnabled: boolean;
  notes: string | null;
  createdAt: number;
  updatedAt: number;
  owners: BotOwner[];
};

export async function listAdminBots(): Promise<AdminBot[]> {
  const res = await request<{ bots: AdminBot[] }>('/bots/admin');
  return res.bots;
}

/** expiresAt 用 YYYY-MM-DD；传 null 表示永久 */
export async function assignBotToUser(
  botId: string,
  userId: string,
  expiresAt: string | null,
): Promise<void> {
  await request(`/bots/${encodeURIComponent(botId)}/assign`, {
    method: 'POST',
    body: JSON.stringify({ userId, expiresAt }),
  });
}

export async function unassignBotFromUser(botId: string, userId: string): Promise<void> {
  await request(`/bots/${encodeURIComponent(botId)}/assign/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
}

export async function deleteAdminBot(botId: string): Promise<void> {
  await request(`/bots/${encodeURIComponent(botId)}`, { method: 'DELETE' });
}
