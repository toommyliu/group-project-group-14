import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import {
  importRoster,
  previewRosterImport,
  rosterQuery,
  type RosterImport,
} from '@/api/roster';

export type ImportState =
  | { kind: 'idle' }
  | { kind: 'checking'; file: File }
  | { kind: 'unchecked'; file: File; error: unknown }
  | { kind: 'review'; file: File; preview: RosterImport; rechecked: boolean }
  | { kind: 'importing'; file: File; preview: RosterImport }
  | { kind: 'failed'; file: File; preview: RosterImport; error: unknown };

export function useRosterImport(onImported: (result: RosterImport) => void) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ImportState>({ kind: 'idle' });
  const latest = useRef(0);

  async function check(file: File, rechecked = false) {
    const request = ++latest.current;
    setState({ kind: 'checking', file });
    try {
      const preview = await previewRosterImport(file);
      if (request === latest.current)
        setState({ kind: 'review', file, preview, rechecked });
    } catch (error) {
      if (request === latest.current)
        setState({ kind: 'unchecked', file, error });
    }
  }

  async function confirm(file: File, preview: RosterImport) {
    const request = ++latest.current;
    setState({ kind: 'importing', file, preview });
    try {
      const result = await importRoster(file, preview);
      await queryClient.invalidateQueries({ queryKey: rosterQuery.queryKey });
      if (request !== latest.current) return;
      setState({ kind: 'idle' });
      onImported(result);
    } catch (error) {
      if (request !== latest.current) return;
      if (error instanceof ApiError && error.status === 409)
        return void check(file, true);
      setState({ kind: 'failed', file, preview, error });
    }
  }

  function cancel() {
    latest.current++;
    setState({ kind: 'idle' });
  }

  return { state, check, confirm, cancel };
}
