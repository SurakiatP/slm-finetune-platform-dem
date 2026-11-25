import { loadEnv } from 'vite';
const env = { ...loadEnv('production', process.cwd(), ''), ...process.env };
const authUrl = env.VITE_AUTH_URL || '/auth';
if (!authUrl.startsWith('/') || authUrl.startsWith('//')) {
  const url = new URL(authUrl);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) {
    throw new Error('VITE_AUTH_URL must be a same-origin path, HTTPS URL, or local development HTTP URL');
  }
  if (url.username || url.password || url.search || url.hash) throw new Error('VITE_AUTH_URL cannot contain credentials, query or fragment');
}
for (const key of ['VITE_AUTH_REALM', 'VITE_AUTH_CLIENT_ID']) {
  if (env[key] && !/^[a-zA-Z0-9._-]+$/.test(env[key])) throw new Error(`${key} contains invalid characters`);
}
console.log('OIDC frontend configuration validated (PostgreSQL credentials are server-side only).');
