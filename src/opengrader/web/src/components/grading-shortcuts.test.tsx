// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { usePreferences } from '@/stores/preferences';
import { useGradingHotkeys } from '@/hooks/use-grading-hotkeys';
import { GradingShortcuts } from './grading-shortcuts';

let root: Root;
let container: HTMLDivElement;
const nextSubmission = vi.fn();

function GradingScreen() {
  useGradingHotkeys([{ action: 'nextSubmission', callback: nextSubmission }]);
  return <GradingShortcuts />;
}

function button(label: string) {
  const element = [...document.querySelectorAll('button')].find((element) =>
    (element.getAttribute('aria-label') ?? element.textContent ?? '')
      .trimStart()
      .startsWith(label)
  );
  if (!element) throw new Error(`Missing button: ${label}`);
  return element;
}

async function click(label: string) {
  await act(async () => {
    const element = button(label);
    element.focus();
    element.click();
  });
}

async function press(key: string, options: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...options,
  });
  await act(async () => document.activeElement?.dispatchEvent(event));
  await act(async () =>
    document.activeElement?.dispatchEvent(
      new KeyboardEvent('keyup', {
        key,
        bubbles: true,
        ...options,
      })
    )
  );
  return event;
}

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  nextSubmission.mockClear();
  usePreferences.setState({ gradingBindings: {}, gradingShortcuts: true });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<GradingScreen />));
  await click('Shortcuts');
  await click('Customize');
  await click('Change shortcut for next submission:');
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

it('records a combination, persists it, and waits through modifier, repeat, and IME events', async () => {
  await press('Shift', { shiftKey: true });
  await press('J', { repeat: true });
  await press('J', { isComposing: true });
  expect(usePreferences.getState().gradingBindings).toEqual({});
  expect(
    button('Recording shortcut for next submission:').getAttribute(
      'aria-pressed'
    )
  ).toBe('true');

  const event = await press('J', { shiftKey: true });
  expect(event.defaultPrevented).toBe(true);
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: 'Shift+J',
  });
  expect(
    JSON.parse(localStorage.getItem('opengrader.grading-bindings')!)
  ).toEqual({ nextSubmission: 'Shift+J' });
  expect(
    button('Change shortcut for next submission:').getAttribute('aria-pressed')
  ).toBe('false');
});

it('keeps recording after conflicts and reserved keys without changing the saved binding', async () => {
  await press('ArrowLeft');
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    'Already used for previous submission'
  );
  expect(usePreferences.getState().gradingBindings).toEqual({});
  await press('Enter');
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    'activate controls'
  );
  await press('j');
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: 'J',
  });
  expect(document.querySelector('[role="alert"]')).toBeNull();
});

it.each(['Escape', 'Tab'])(
  'cancels with %s without assigning it',
  async (key) => {
    const event = await press(key);
    expect(event.defaultPrevented).toBe(key === 'Escape');
    expect(
      button('Change shortcut for next submission:').getAttribute(
        'aria-pressed'
      )
    ).toBe('false');
    await press('j');
    expect(usePreferences.getState().gradingBindings).toEqual({});
  }
);

it.each(['Backspace', 'Delete'])('removes a binding with %s', async (key) => {
  await press(key);
  expect(usePreferences.getState().gradingBindings).toEqual({
    nextSubmission: null,
  });
  expect(
    button('Change shortcut for next submission:').getAttribute('aria-pressed')
  ).toBe('false');
});

it('stops capture when focus leaves the shortcut button', async () => {
  await act(async () => button('Done').focus());
  const event = await press('j');
  expect(event.defaultPrevented).toBe(false);
  expect(usePreferences.getState().gradingBindings).toEqual({});
});

it('recognizes the plus key and checks it against existing shifted punctuation bindings', async () => {
  await press('+', { code: 'Equal', shiftKey: true });
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    'Already used for zoom in'
  );
  await click('Change shortcut for zoom in:');
  await press('Backspace');
  await click('Change shortcut for next submission:');
  await press('+', { code: 'Equal', shiftKey: true });
  expect(usePreferences.getState().gradingBindings).toEqual({
    zoomIn: null,
    nextSubmission: 'Shift++',
  });
  expect(nextSubmission).not.toHaveBeenCalled();
  await click('Shortcuts');
  await press('+', { code: 'Equal', shiftKey: true });
  expect(nextSubmission).toHaveBeenCalledOnce();
});

it.each([
  ['?', 'Slash', 'show shortcuts'],
  [')', 'Digit0', 'fit image'],
])(
  'detects %s conflicts using the recorded keyboard event',
  async (key, code, action) => {
    await press(key, { code, shiftKey: true });
    expect(document.querySelector('[role="alert"]')?.textContent).toContain(
      `Already used for ${action}`
    );
    expect(usePreferences.getState().gradingBindings).toEqual({});
  }
);

it('records and replays a shifted character without assuming its US keyboard position', async () => {
  await act(async () =>
    usePreferences.setState({ gradingBindings: { focusScore: 'Shift+1' } })
  );
  await press('!', { code: 'Digit2', shiftKey: true });
  expect(document.querySelector('[role="alert"]')).toBeNull();
  expect(usePreferences.getState().gradingBindings.nextSubmission).toBe(
    'Shift+!'
  );
  await click('Shortcuts');
  await press('!', { code: 'Digit2', shiftKey: true });
  expect(nextSubmission).toHaveBeenCalledOnce();
});

it('keeps browser protection for shifted keys', async () => {
  vi.stubGlobal('navigator', { platform: 'Win32', userAgent: '' });
  await press(')', { code: 'Digit0', shiftKey: true, ctrlKey: true });
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    'browser or system'
  );
  expect(usePreferences.getState().gradingBindings).toEqual({});
});
