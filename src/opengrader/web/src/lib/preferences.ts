export type Preferences = {
  editorWidth: number;
  pdfAction: 'download' | 'open';
  theme: 'dark' | 'light' | 'system';
};

type Definition<T> = {
  storageKey: string;
  defaultValue: T;
  parse: (value: string) => T | undefined;
  serialize: (value: T) => string;
};

const definitions: { [K in keyof Preferences]: Definition<Preferences[K]> } = {
  editorWidth: {
    storageKey: 'opengrader.assignment-editor-width',
    defaultValue: 50,
    parse: (value) => {
      const width = Number(value);
      return Number.isFinite(width) && width >= 25 && width <= 75
        ? width
        : undefined;
    },
    serialize: String,
  },
  pdfAction: {
    storageKey: 'opengrader.pdf-action',
    defaultValue: 'download',
    parse: (value) =>
      value === 'open' || value === 'download' ? value : undefined,
    serialize: (value) => value,
  },
  theme: {
    storageKey: 'theme',
    defaultValue: 'system',
    parse: (value) =>
      value === 'dark' || value === 'light' || value === 'system'
        ? value
        : undefined,
    serialize: (value) => value,
  },
};

const listeners = new Map<keyof Preferences, Set<() => void>>();
const sessionValues: { [K in keyof Preferences]?: Preferences[K] } = {};

function get<K extends keyof Preferences>(key: K): Preferences[K] {
  const sessionValue = sessionValues[key];
  if (sessionValue !== undefined) return sessionValue;
  const definition = definitions[key];
  try {
    const value = localStorage.getItem(definition.storageKey);
    return value === null
      ? definition.defaultValue
      : (definition.parse(value) ?? definition.defaultValue);
  } catch {
    return definition.defaultValue;
  }
}

function set<K extends keyof Preferences>(
  key: K,
  value: NoInfer<Preferences[K]>
): void {
  const definition = definitions[key];
  try {
    localStorage.setItem(definition.storageKey, definition.serialize(value));
    delete sessionValues[key];
  } catch {
    // Share the preference within this tab even when persistence is unavailable.
    sessionValues[key] = value;
  }
  listeners.get(key)?.forEach((listener) => listener());
}

/** Observe same-tab updates and changes or removals made in other tabs. */
function subscribe(key: keyof Preferences, listener: () => void) {
  const subscribers = listeners.get(key) ?? new Set<() => void>();
  listeners.set(key, subscribers);
  subscribers.add(listener);
  const handleStorage = (event: StorageEvent) => {
    try {
      if (event.storageArea !== localStorage) return;
    } catch {
      return;
    }
    if (event.key === null || event.key === definitions[key].storageKey) {
      delete sessionValues[key];
      listener();
    }
  };
  window.addEventListener('storage', handleStorage);
  return () => {
    subscribers.delete(listener);
    if (!subscribers.size) listeners.delete(key);
    window.removeEventListener('storage', handleStorage);
  };
}

export const preferences = { get, set, subscribe };
