import { afterEach, expect, it, vi } from 'vitest';
import { parseGradingBindings, shortcutError } from './grading-shortcuts';

afterEach(() => vi.unstubAllGlobals());

it('restores recorded characters and existing base-key bindings', () => {
  expect(
    parseGradingBindings(
      '{"zoomIn":"Shift++","showShortcuts":"Shift+?","nextSubmission":"Shift+!","focusScore":"Shift+Ж"}'
    )
  ).toEqual({
    zoomIn: 'Shift++',
    showShortcuts: 'Shift+?',
    nextSubmission: 'Shift+!',
    focusScore: 'Shift+Ж',
  });
  expect(
    parseGradingBindings('{"zoomIn":"Shift+=","showShortcuts":"Shift+/"}')
  ).toEqual({
    zoomIn: 'Shift+=',
    showShortcuts: 'Shift+/',
  });
});

it('reserves the primary modifier of the current platform', () => {
  vi.stubGlobal('navigator', { platform: 'MacIntel', userAgent: '' });
  expect(shortcutError('nextSubmission', 'Control+J', {})).toBeUndefined();
  expect(shortcutError('nextSubmission', 'Meta+L', {})).toContain('browser');
  expect(shortcutError('nextSubmission', 'Meta+[', {})).toContain('browser');

  vi.stubGlobal('navigator', { platform: 'Win32', userAgent: '' });
  expect(shortcutError('nextSubmission', 'Control+J', {})).toContain('browser');
  expect(shortcutError('nextSubmission', 'Meta+K', {})).toContain('system');
  expect(shortcutError('nextSubmission', 'Alt+ArrowLeft', {})).toContain(
    'browser'
  );
});

it.each(['MacIntel', 'Win32', 'Linux x86_64'])(
  'keeps browser restrictions, control navigation, and app shortcuts on %s',
  (platform) => {
    vi.stubGlobal('navigator', { platform, userAgent: '' });
    expect(shortcutError('nextSubmission', 'Mod+Shift+T', {})).toContain(
      'browser'
    );
    expect(shortcutError('nextSubmission', 'F5', {})).toContain('browser');
    expect(shortcutError('nextSubmission', 'F2', {})).toBeUndefined();
    expect(shortcutError('nextSubmission', 'Mod+B', {})).toContain('sidebar');
    expect(shortcutError('nextSubmission', 'Shift+Tab', {})).toContain(
      'activate controls'
    );
    expect(shortcutError('nextSubmission', 'Enter', {})).toContain(
      'activate controls'
    );
    expect(shortcutError('save', 'Mod+S', {})).toBeUndefined();
    expect(shortcutError('saveAndNext', 'Mod+Enter', {})).toBeUndefined();
  }
);

it('detects conflicts with defaults and platform modifier aliases', () => {
  vi.stubGlobal('navigator', { platform: 'MacIntel', userAgent: '' });
  expect(shortcutError('nextSubmission', 'ArrowLeft', {})).toContain(
    'previous submission'
  );
  expect(
    shortcutError('nextSubmission', 'Meta+K', { focusScore: 'Mod+K' })
  ).toContain('focus score');
  expect(
    shortcutError('nextSubmission', 'ArrowLeft', { previousSubmission: null })
  ).toBeUndefined();
});

it('validates saved keys before normalization and checks conflicts after loading all overrides', () => {
  vi.stubGlobal('navigator', { platform: 'Win32', userAgent: '' });
  expect(
    parseGradingBindings('{"nextSubmission":"NotAMod+J"}')
  ).toBeUndefined();
  expect(
    parseGradingBindings('{"nextSubmission":"Mod+Shift+T"}')
  ).toBeUndefined();
  expect(
    parseGradingBindings('{"nextSubmission":"ctrl+k","focusScore":"Mod+K"}')
  ).toBeUndefined();
  expect(
    parseGradingBindings(
      '{"nextSubmission":"ArrowLeft","previousSubmission":"ArrowRight","focusScore":"ctrl+k"}'
    )
  ).toEqual({
    nextSubmission: 'ArrowLeft',
    previousSubmission: 'ArrowRight',
    focusScore: 'Mod+K',
  });
});
