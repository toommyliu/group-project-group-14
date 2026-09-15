import { queryOptions } from '@tanstack/react-query';
import { client } from './queries';
import type { components } from './schema';

export type TerminalSession = components['schemas']['TerminalRecord'];
export type TerminalCatalog = components['schemas']['TerminalCatalog'];

async function result<T>({
  data,
  error,
  response,
}: {
  data?: T;
  error?: unknown;
  response: Response;
}): Promise<T> {
  if (!response.ok) {
    const detail =
      error && typeof error === 'object' && 'detail' in error
        ? error.detail
        : null;
    throw new Error(
      typeof detail === 'string'
        ? detail
        : `Request failed (${response.status}).`
    );
  }
  if (data === undefined)
    throw new Error('The server returned an empty response.');
  return data;
}

export function terminalsQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['terminals', assignmentId],
    queryFn: async ({ signal }) =>
      result(
        await client.GET('/api/terminals', {
          params: { query: { assignment_id: assignmentId } },
          signal,
        })
      ),
    refetchInterval: 5000,
  });
}

export async function createTerminal(assignmentId: string) {
  return result(
    await client.POST('/api/terminals', {
      body: { assignment_id: assignmentId, kind: 'shell' },
    })
  );
}

export async function startTerminal(id: string) {
  return result(
    await client.POST('/api/terminals/{session_id}/start', {
      params: { path: { session_id: id } },
      body: { cols: 100, rows: 30 },
    })
  );
}

export async function stopTerminal(id: string) {
  return result(
    await client.POST('/api/terminals/{session_id}/stop', {
      params: { path: { session_id: id } },
    })
  );
}

export async function removeTerminal(id: string) {
  const { response } = await client.DELETE('/api/terminals/{session_id}', {
    params: { path: { session_id: id } },
  });
  if (!response.ok)
    throw new Error(
      'Could not remove the session. Stop it first, then try again.'
    );
}

export async function renameTerminal(id: string, title: string) {
  return result(
    await client.PATCH('/api/terminals/{session_id}', {
      params: { path: { session_id: id } },
      body: { title },
    })
  );
}

export async function reorderTerminals(assignmentId: string, ids: string[]) {
  return result(
    await client.PUT('/api/terminals/order', {
      body: { assignment_id: assignmentId, ids },
    })
  );
}
