import { useCallback, useSyncExternalStore, type SetStateAction } from 'react';
import { preferences, type Preferences } from '@/lib/preferences';

/** Read and update a persistent preference. */
export function usePreference<K extends keyof Preferences>(key: K) {
  const subscribe = useCallback(
    (listener: () => void) => preferences.subscribe(key, listener),
    [key]
  );
  const getSnapshot = useCallback(() => preferences.get(key), [key]);
  const value = useSyncExternalStore(subscribe, getSnapshot);
  const setValue = useCallback(
    (next: SetStateAction<Preferences[K]>) => {
      preferences.set(
        key,
        typeof next === 'function' ? next(preferences.get(key)) : next
      );
    },
    [key]
  );
  return [value, setValue] as const;
}
