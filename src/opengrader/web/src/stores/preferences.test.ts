import { beforeEach, expect, it, vi } from 'vitest';

const stored = new Map<string, string>();
let blocked = false;
const storage = {
  getItem: (key: string) => stored.get(key) ?? null,
  setItem: (key: string, value: string) => {
    if (blocked) throw new Error('Storage is unavailable');
    stored.set(key, value);
  },
};
let target = new EventTarget();

async function load() {
  vi.resetModules();
  target = new EventTarget();
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('window', target);
  return import('./preferences');
}

beforeEach(() => {
  stored.clear();
  blocked = false;
});

it('reads saved preferences and falls back to defaults for invalid values', async () => {
  stored.set('opengrader.pdf-action', 'open');
  stored.set('opengrader.sidebar-width', '9000');
  stored.set('opengrader.grading-shortcuts', 'yes');
  const { usePreferences } = await load();
  expect(usePreferences.getState()).toEqual({
    anonymousGrading: false,
    gradingShortcuts: true,
    gradingBindings: {},
    editorWidth: 50,
    editorMode: 'visual',
    sidebarWidth: 256,
    sidebarExpanded: true,
    pdfAction: 'open',
    theme: 'system',
    rosterNameOrder: 'last',
  });
});

it('persists updates under the existing storage keys', async () => {
  const { setPreference, usePreferences } = await load();
  setPreference('theme', 'dark');
  setPreference('gradingShortcuts', false);
  expect(stored.get('theme')).toBe('dark');
  expect(stored.get('opengrader.grading-shortcuts')).toBe('false');
  expect(usePreferences.getState().theme).toBe('dark');
});

it('keeps updates for this tab when storage is unavailable', async () => {
  const { setPreference, usePreferences } = await load();
  blocked = true;
  setPreference('editorWidth', 40);
  expect(usePreferences.getState().editorWidth).toBe(40);
  expect(stored.has('opengrader.assignment-editor-width')).toBe(false);
});

it('follows changes and removals from other tabs', async () => {
  const { usePreferences } = await load();
  stored.set('opengrader.sidebar-expanded', 'false');
  target.dispatchEvent(
    Object.assign(new Event('storage'), {
      key: 'opengrader.sidebar-expanded',
      storageArea: storage,
    })
  );
  expect(usePreferences.getState().sidebarExpanded).toBe(false);

  stored.clear();
  target.dispatchEvent(
    Object.assign(new Event('storage'), { key: null, storageArea: storage })
  );
  expect(usePreferences.getState().sidebarExpanded).toBe(true);
});

it('restores custom shortcuts and disabled actions after reloading', async () => {
  const { setPreference } = await load();
  setPreference('gradingBindings', { nextSubmission: 'J', clearGrade: null });
  const { usePreferences } = await load();
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: 'J',
    clearGrade: null,
  });
});

it.each([
  '{broken',
  '[]',
  '{"nextSubmission":123}',
  '{"nextSubmission":"ArrowLeft"}',
  '{"nextSubmission":"Mod+L"}',
  '{"nextSubmission":"Teleport"}',
])(
  'falls back safely for invalid or conflicting stored shortcuts: %s',
  async (value) => {
    stored.set('opengrader.grading-bindings', value);
    const { usePreferences } = await load();
    expect(usePreferences.getState().gradingBindings).toEqual({});
  }
);

it('restores swapped bindings together and follows shortcut changes across tabs', async () => {
  stored.set(
    'opengrader.grading-bindings',
    '{"nextSubmission":"ArrowLeft","previousSubmission":"ArrowRight"}'
  );
  const { usePreferences } = await load();
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: 'ArrowLeft',
    previousSubmission: 'ArrowRight',
  });
  stored.set('opengrader.grading-bindings', '{"nextSubmission":"J"}');
  target.dispatchEvent(
    Object.assign(new Event('storage'), {
      key: 'opengrader.grading-bindings',
      storageArea: storage,
    })
  );
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: 'J',
  });
});
