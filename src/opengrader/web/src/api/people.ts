import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type People = components['schemas']['People'];

export const peopleQuery = queryOptions({
  queryKey: ['people'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/api/people', { signal })),
});

export async function addGrader(email: string) {
  return readData(
    await client.POST('/api/people/graders', { body: { email } })
  );
}

export async function removeGrader(email: string) {
  return readData(
    await client.POST('/api/people/graders/remove', { body: { email } })
  );
}
