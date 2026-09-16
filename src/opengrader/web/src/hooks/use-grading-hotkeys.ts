import {
  useHotkeys,
  type UseHotkeyDefinition,
  type UseHotkeyOptions,
} from '@tanstack/react-hotkeys';

export function useGradingHotkeys(
  definitions: UseHotkeyDefinition[],
  options: UseHotkeyOptions
) {
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
    { ...options, preventDefault: false, stopPropagation: false }
  );
}
