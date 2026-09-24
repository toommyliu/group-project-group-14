import {
  useHotkeys,
  type UseHotkeyDefinition,
  type UseHotkeyOptions,
} from '@tanstack/react-hotkeys';
import { usePreferences } from '@/stores/preferences';

export function useGradingHotkeys(
  definitions: UseHotkeyDefinition[],
  options: UseHotkeyOptions
) {
  const enabled = usePreferences((state) => state.gradingShortcuts);
  useHotkeys(
    definitions.map((definition) => ({
      ...definition,
      callback: (event, context) => {
        if (
          event.target instanceof Element &&
          event.target.closest(
            '[role="dialog"], [role="alertdialog"], [role="listbox"], [role="combobox"]'
          )
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        definition.callback(event, context);
      },
    })),
    {
      ...options,
      enabled: enabled && options.enabled !== false,
      preventDefault: false,
      stopPropagation: false,
    }
  );
}
