import { useSyncExternalStore } from 'react';

// Keep in sync with the editor's min-[65rem] navigation styles.
const query = '(min-width: 65rem)';

function subscribe(callback: () => void) {
  const media = window.matchMedia(query);
  media.addEventListener('change', callback);
  return () => media.removeEventListener('change', callback);
}

export function useWideAssignmentLayout() {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false
  );
}
