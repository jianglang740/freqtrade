/*
 * 机器人密码加密：AES-256-GCM。
 * 存储格式 v1:<ivB64>:<tagB64>:<cipherB64>；密钥只从环境变量读，永不返回给前端。
 */
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { env } from '../env.js';

const KEY = Buffer.from(env.ENCRYPTION_KEY, 'base64');
const IV_BYTES = 12;
const PREFIX = 'v1';

export function encryptSecret(plain: string): string {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv('aes-256-gcm', KEY, iv);
  const encrypted = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return [PREFIX, iv.toString('base64'), tag.toString('base64'), encrypted.toString('base64')].join(
    ':',
  );
}

export function decryptSecret(payload: string): string {
  const parts = payload.split(':');
  if (parts.length !== 4 || parts[0] !== PREFIX) {
    throw new Error('Invalid encrypted secret format');
  }
  const [, ivB64, tagB64, cipherB64] = parts;
  const decipher = createDecipheriv('aes-256-gcm', KEY, Buffer.from(ivB64, 'base64'));
  decipher.setAuthTag(Buffer.from(tagB64, 'base64'));
  const plain = Buffer.concat([
    decipher.update(Buffer.from(cipherB64, 'base64')),
    decipher.final(),
  ]);
  return plain.toString('utf8');
}
