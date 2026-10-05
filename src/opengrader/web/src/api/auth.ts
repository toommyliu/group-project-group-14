import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type Role = NonNullable<components['schemas']['AuthStatus']['role']>;

export const authQuery = queryOptions({
  queryKey: ['auth'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/auth/me', { signal })),
});

export async function logout() {
  await client.POST('/auth/logout');
}
