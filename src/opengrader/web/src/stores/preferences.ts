import { create } from 'zustand';
import {
  parseGradingBindings,
  type GradingBindings,
} from '@/lib/grading-shortcuts';

export type Preferences = {
  anonymousGrading: boolean;
  gradingShortcuts: boolean;
  gradingBindings: GradingBindings;
  editorWidth: number;
  editorMode: 'visual' | 'markdown';
  sidebarWidth: number;
  sidebarExpanded: boolean;
  pdfAction: 'download' | 'open';
  rosterNameOrder: 'last' | 'first';
  theme: 'dark' | 'light' | 'system';
};

export const sidebarWidthBounds = { min: 224, max: 400, default: 256 } as const;

type Definition<T> = {
  storageKey: string;
  defaultValue: T;
  parse: (value: string) => T | undefined;
  serialize: (value: T) => string;
};

const definitions: { [K in keyof Preferences]: Definition<Preferences[K]> } = {
  anonymousGrading: {
    storageKey: 'opengrader.anonymous-grading',
    defaultValue: false,
    parse: (value) =>
      value === 'true' ? true : value === 'false' ? false : undefined,
    serialize: String,
  },
  gradingBindings: {
    storageKey: 'opengrader.grading-bindings',
    defaultValue: {},
    parse: parseGradingBindings,
    serialize: JSON.stringify,
  },
  gradingShortcuts: {
    storageKey: 'opengrader.grading-shortcuts',
    defaultValue: true,
    parse: (value) =>
      value === 'true' ? true : value === 'false' ? false : undefined,
    serialize: String,
  },
  sidebarExpanded: {
    storageKey: 'opengrader.sidebar-expanded',
    defaultValue: true,
    parse: (value) =>
      value === 'true' ? true : value === 'false' ? false : undefined,
    serialize: String,
  },
  sidebarWidth: {
    storageKey: 'opengrader.sidebar-width',
    defaultValue: sidebarWidthBounds.default,
    parse: (value) => {
      const width = Number(value);
      return Number.isFinite(width) &&
        width >= sidebarWidthBounds.min &&
        width <= sidebarWidthBounds.max
        ? width
        : undefined;
    },
    serialize: String,
  },
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
  editorMode: {
    storageKey: 'opengrader.assignment-editor-mode',
    defaultValue: 'visual',
    parse: (value) =>
      value === 'visual' || value === 'markdown' ? value : undefined,
    serialize: (value) => value,
  },
  pdfAction: {
    storageKey: 'opengrader.pdf-action',
    defaultValue: 'download',
    parse: (value) =>
      value === 'open' || value === 'download' ? value : undefined,
    serialize: (value) => value,
  },
  rosterNameOrder: {
    storageKey: 'opengrader.roster-name-order',
    defaultValue: 'last',
    parse: (value) =>
      value === 'last' || value === 'first' ? value : undefined,
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

function read<K extends keyof Preferences>(key: K): Preferences[K] {
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

function readAll(keys: (keyof Preferences)[]) {
  return Object.fromEntries(keys.map((key) => [key, read(key)]));
}

const keys = Object.keys(definitions) as (keyof Preferences)[];

export const usePreferences = create<Preferences>(
  () => readAll(keys) as Preferences
);

export function setPreference<K extends keyof Preferences>(
  key: K,
  value: NoInfer<Preferences[K]>
) {
  const definition = definitions[key];
  try {
    localStorage.setItem(definition.storageKey, definition.serialize(value));
  } catch {
    // The store still holds the value for this tab.
  }
  usePreferences.setState({ [key]: value });
}

window.addEventListener('storage', (event) => {
  try {
    if (event.storageArea !== localStorage) return;
  } catch {
    return;
  }
  const changed = keys.filter(
    (key) => event.key === null || event.key === definitions[key].storageKey
  );
  if (changed.length) usePreferences.setState(readAll(changed));
});
