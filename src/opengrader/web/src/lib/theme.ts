import { setPreference, usePreferences } from '@/stores/preferences';

const darkScheme = window.matchMedia('(prefers-color-scheme: dark)');

function systemTheme() {
  return darkScheme.matches ? 'dark' : 'light';
}

function disableTransitionsTemporarily() {
  const style = document.createElement('style');
  style.appendChild(
    document.createTextNode(
      '*,*::before,*::after{-webkit-transition:none!important;transition:none!important}'
    )
  );
  document.head.appendChild(style);

  return () => {
    window.getComputedStyle(document.body);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        style.remove();
      });
    });
  };
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.closest("input, textarea, select, [contenteditable='true']") !== null
  );
}

function applyTheme() {
  const { theme } = usePreferences.getState();
  const restoreTransitions = disableTransitionsTemporarily();
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(theme === 'system' ? systemTheme() : theme);
  restoreTransitions();
}

// TODO: migrate
function toggleTheme(event: KeyboardEvent) {
  if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
  if (isEditableTarget(event.target) || event.key.toLowerCase() !== 'd') return;
  const { theme } = usePreferences.getState();
  const resolved = theme === 'system' ? systemTheme() : theme;
  setPreference('theme', resolved === 'dark' ? 'light' : 'dark');
}

function followSystemTheme() {
  if (usePreferences.getState().theme === 'system') applyTheme();
}

export function syncTheme() {
  applyTheme();
  const unsubscribe = usePreferences.subscribe((state, previous) => {
    if (state.theme !== previous.theme) applyTheme();
  });
  darkScheme.addEventListener('change', followSystemTheme);
  window.addEventListener('keydown', toggleTheme);
  return () => {
    unsubscribe();
    darkScheme.removeEventListener('change', followSystemTheme);
    window.removeEventListener('keydown', toggleTheme);
  };
}
