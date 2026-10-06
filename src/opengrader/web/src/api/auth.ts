import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type AuthStatus = components['schemas']['AuthStatus'];

export const authQuery = queryOptions({
  queryKey: ['auth'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/auth/me', { signal })),
});

export async function logout() {
  await client.POST('/auth/logout');
}

export function admitted(auth: AuthStatus) {
  return !auth.enabled || auth.admin || Boolean(auth.user);
}
