import {
  parseHotkey,
  useHotkeys,
  type UseHotkeyDefinition,
  type UseHotkeyOptions,
} from '@tanstack/react-hotkeys';
import {
  gradingKey,
  shortcutLabel,
  type GradingAction,
} from '@/lib/grading-shortcuts';
import { usePreferences } from '@/stores/preferences';

type GradingHotkeyDefinition = Omit<UseHotkeyDefinition, 'hotkey'> & {
  action: GradingAction;
};

export function useGradingShortcutHints() {
  const enabled = usePreferences((state) => state.gradingShortcuts);
  const bindings = usePreferences((state) => state.gradingBindings);
  return {
    shortcut: (action: GradingAction) =>
      enabled ? shortcutLabel(gradingKey(action, bindings)) : '',
    shortcutAria: (action: GradingAction) => {
      const key = gradingKey(action, bindings);
      if (!enabled || key === null) return undefined;
      const parsed = parseHotkey(key);
      return [...parsed.modifiers, parsed.key].join('+');
    },
  };
}

export function useGradingHotkeys(
  definitions: GradingHotkeyDefinition[],
  options: UseHotkeyOptions = {}
) {
  const enabled = usePreferences((state) => state.gradingShortcuts);
  const bindings = usePreferences((state) => state.gradingBindings);
  useHotkeys(
    definitions.flatMap(({ action, callback, options: actionOptions }) => {
      const hotkey = gradingKey(action, bindings);
      if (hotkey === null) return [];
      const parsed = parseHotkey(hotkey);
      return [
        {
          hotkey,
          options: {
            ...actionOptions,
            ignoreInputs: !(
              (action === 'save' || action === 'saveAndNext') &&
              (parsed.ctrl || parsed.meta)
            ),
          },
          callback: (event, context) => {
            if (
              event.defaultPrevented ||
              event.isComposing ||
              event.keyCode === 229
            )
              return;
            if (
              event.target instanceof Element &&
              event.target.closest(
                '[role="dialog"], [role="alertdialog"], [role="listbox"], [role="combobox"], [role="menu"], [role="tablist"], [role="slider"], [role="spinbutton"]'
              )
            )
              return;
            event.preventDefault();
            event.stopPropagation();
            callback(event, context);
          },
        } satisfies UseHotkeyDefinition,
      ];
    }),
    {
      ...options,
      enabled: enabled && options.enabled !== false,
      preventDefault: false,
      stopPropagation: false,
    }
  );
}
