import { usePreferences } from '@/stores/preferences';

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

function applyTheme() {
  const { theme } = usePreferences.getState();
  const restoreTransitions = disableTransitionsTemporarily();
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(theme === 'system' ? systemTheme() : theme);
  restoreTransitions();
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
  return () => {
    unsubscribe();
    darkScheme.removeEventListener('change', followSystemTheme);
  };
}
