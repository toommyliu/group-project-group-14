import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type TerminalSession = components['schemas']['TerminalRecord'];
export type TerminalCatalog = components['schemas']['TerminalCatalog'];

export function terminalsQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['terminals', assignmentId],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/api/terminals', {
          params: { query: { assignment_id: assignmentId } },
          signal,
        })
      ),
    refetchInterval: 5000,
  });
}

export async function createTerminal(assignmentId: string) {
  return readData(
    await client.POST('/api/terminals', {
      body: { assignment_id: assignmentId, kind: 'shell' },
    })
  );
}

export async function startTerminal(id: string) {
  return readData(
    await client.POST('/api/terminals/{session_id}/start', {
      params: { path: { session_id: id } },
      body: { cols: 100, rows: 30 },
    })
  );
}

export async function stopTerminal(id: string) {
  return readData(
    await client.POST('/api/terminals/{session_id}/stop', {
      params: { path: { session_id: id } },
    })
  );
}

export async function removeTerminal(id: string) {
  await client.DELETE('/api/terminals/{session_id}', {
    params: { path: { session_id: id } },
  });
}

export async function renameTerminal(id: string, title: string) {
  return readData(
    await client.PATCH('/api/terminals/{session_id}', {
      params: { path: { session_id: id } },
      body: { title },
    })
  );
}

export async function reorderTerminals(assignmentId: string, ids: string[]) {
  return readData(
    await client.PUT('/api/terminals/order', {
      body: { assignment_id: assignmentId, ids },
    })
  );
}
