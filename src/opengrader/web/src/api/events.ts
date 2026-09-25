import type { QueryClient } from '@tanstack/react-query';
import { client, readData } from './client';
import { workspaceQuery } from './workspace';

/** Subscribe before starting route loads so a change cannot fall between them. */
export async function observeWorkspace(queries: QueryClient) {
  const info = readData(await client.GET('/api/workspace'));
  queries.setQueryData(workspaceQuery.queryKey, info);
  const events = new EventSource(`/api/events?after=${info.event_cursor}`);
  const pending = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  events.onmessage = (message) => {
    const value: unknown = JSON.parse(message.data);
    if (
      typeof value !== 'object' ||
      value === null ||
      !('assignment_id' in value) ||
      typeof value.assignment_id !== 'string'
    )
      return;
    pending.add(value.assignment_id);
    // An "assignment" event also fires when a folder session converts to a
    // workspace: other tabs need to re-read /api/workspace to notice.
    if ('kind' in value && value.kind === 'assignment')
      void queries.invalidateQueries({ queryKey: workspaceQuery.queryKey });
    timer ??= setTimeout(() => {
      for (const id of pending)
        void queries.invalidateQueries({ queryKey: ['assignments', id] });
      pending.clear();
      timer = undefined;
      void queries.invalidateQueries({
        queryKey: ['assignments'],
        exact: true,
      });
      void queries.invalidateQueries({ queryKey: ['feedback-jobs'] });
    }, 16);
  };
  return () => {
    clearTimeout(timer);
    events.close();
  };
}
