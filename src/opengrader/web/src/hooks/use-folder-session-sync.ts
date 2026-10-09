import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { rosterQuery } from '@/api/roster';
import { syncFolderSession, workspaceQuery } from '@/api/workspace';
import { ApiError } from '@/api/client';
import { errorMessage } from '@/lib/error-message';

/**
 * Syncs a folder session on mount and whenever the window regains focus.
 * Never on a timer, and never two requests at once. Pulled cells are saved
 * through the normal save path, so open queries refresh from the resulting
 * grade/submission events; this hook only surfaces sync-specific feedback.
 */
export function useFolderSessionSync() {
  const [problems, setProblems] = useState<string[]>([]);
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const queries = useQueryClient();

  const runSync = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    void syncFolderSession()
      .then((result) => {
        // The class folder's roster appeared, changed or went away: the
        // People link and everything that lists students follow.
        if (result.roster_changed) {
          void queries.invalidateQueries({ queryKey: workspaceQuery.queryKey });
          void queries.invalidateQueries({ queryKey: rosterQuery.queryKey });
        }
        setProblems(result.failed > 0 ? result.problems : []);
        const changed = result.updated + result.imported;
        if (changed > 0)
          setNotice(
            `Updated ${changed} grade${changed === 1 ? '' : 's'} from the folder.`
          );
      })
      .catch((cause: unknown) => {
        // A 409 here (workspace converted elsewhere, or the folder moved) is
        // durable until fixed; anything else is likely transient (e.g.
        // offline), and the next focus/visibility change retries.
        if (cause instanceof ApiError) setProblems([errorMessage(cause)]);
      })
      .finally(() => {
        inFlight.current = false;
      });
  }, [queries]);

  useEffect(() => {
    runSync();
    function onVisibilityChange() {
      if (document.visibilityState === 'visible') runSync();
    }
    window.addEventListener('focus', runSync);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('focus', runSync);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [runSync]);

  return {
    problems,
    dismissProblems: () => setProblems([]),
    notice,
    dismissNotice: () => setNotice(''),
  };
}
