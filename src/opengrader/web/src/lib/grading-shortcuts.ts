import {
  areHotkeysEqual,
  detectPlatform,
  matchesKeyboardEvent,
  formatForDisplay,
  normalizeHotkey,
  parseHotkey,
  validateHotkey,
  type Hotkey,
} from '@tanstack/react-hotkeys';

const rubricShortcuts = {
  rubric1: { label: 'Rubric item 1', key: '1' },
  rubric2: { label: 'Rubric item 2', key: '2' },
  rubric3: { label: 'Rubric item 3', key: '3' },
  rubric4: { label: 'Rubric item 4', key: '4' },
  rubric5: { label: 'Rubric item 5', key: '5' },
  rubric6: { label: 'Rubric item 6', key: '6' },
  rubric7: { label: 'Rubric item 7', key: '7' },
  rubric8: { label: 'Rubric item 8', key: '8' },
  rubric9: { label: 'Rubric item 9', key: '9' },
  rubric10: { label: 'Rubric item 10', key: '0' },
} as const;

export const rubricActions = Object.keys(
  rubricShortcuts
) as (keyof typeof rubricShortcuts)[];

export const gradingActions = {
  previousSubmission: {
    label: 'Previous submission',
    key: 'ArrowLeft',
  },
  nextSubmission: {
    label: 'Next submission',
    key: 'ArrowRight',
  },
  previousQuestion: {
    label: 'Previous question',
    key: 'Shift+ArrowLeft',
  },
  nextQuestion: {
    label: 'Next question',
    key: 'Shift+ArrowRight',
  },
  nextUngraded: {
    label: 'Next ungraded response',
    key: 'Z',
  },
  saveAndNext: {
    label: 'Save and next ungraded',
    key: 'Mod+Enter',
  },
  focusScore: { label: 'Focus score', key: 'S' },
  focusFeedback: { label: 'Write feedback', key: 'C' },
  save: { label: 'Save now', key: 'Mod+S' },
  clearGrade: { label: 'Clear grade', key: null },
  zoomIn: { label: 'Zoom in', key: normalizeHotkey('Shift+=') },
  zoomOut: { label: 'Zoom out', key: '-' },
  fitImage: { label: 'Fit image', key: 'Shift+0' },
  rotateImage: { label: 'Rotate image', key: 'Shift+R' },
  showShortcuts: {
    label: 'Show shortcuts',
    key: normalizeHotkey('Shift+/'),
  },
  ...rubricShortcuts,
} as const satisfies Record<string, { label: string; key: Hotkey | null }>;

export type GradingAction = keyof typeof gradingActions;
export type GradingBindings = Partial<Record<GradingAction, Hotkey | null>>;
const gradingActionIds = Object.keys(gradingActions) as GradingAction[];

export function gradingKey(action: GradingAction, bindings: GradingBindings) {
  return bindings[action] === undefined
    ? gradingActions[action].key
    : bindings[action];
}

export function shortcutLabel(key: Hotkey | null) {
  if (key === null) return '';
  if (key === normalizeHotkey('Shift+/')) return '?';
  if (key === normalizeHotkey('Shift+=')) return '+';
  return formatForDisplay(key);
}

const browserKeys = {
  primary: 'A C V X Z Y F G L N T W R P Q O H J D = - 0'.split(' '),
  mac: ['M', ',', '[', ']', 'ArrowLeft', 'ArrowRight'],
  alt: ['ArrowLeft', 'ArrowRight', 'F4'],
  always: ['F1', 'F3', 'F5', 'F6', 'F7', 'F11', 'F12'],
};

function keyError(key: string, event?: KeyboardEvent) {
  const validation = validateHotkey(key);
  const parsed = parseHotkey(key);
  if (
    !validation.valid ||
    validation.warnings.length ||
    parsed.key === undefined
  )
    return 'Choose a letter, number, arrow, or function key, with optional modifiers.';
  if (
    ['Tab', 'Escape'].includes(parsed.key) ||
    (!parsed.ctrl &&
      !parsed.meta &&
      !parsed.alt &&
      ['Enter', 'Space', 'Backspace', 'Delete'].includes(parsed.key))
  )
    return 'This key is used to move between or activate controls. Choose another shortcut.';
  if (areHotkeysEqual(normalizeHotkey(key), 'Mod+B'))
    return 'This shortcut toggles the sidebar. Choose another shortcut.';
  const matchesKey = (key: string) =>
    parsed.key === key ||
    (event !== undefined && matchesKeyboardEvent(event, { ...parsed, key }));
  const mac = detectPlatform() === 'mac';
  if (
    browserKeys.always.some(matchesKey) ||
    ((mac ? parsed.meta : parsed.ctrl) &&
      browserKeys.primary.some(matchesKey)) ||
    (mac
      ? parsed.meta && browserKeys.mac.some(matchesKey)
      : parsed.meta || (parsed.alt && browserKeys.alt.some(matchesKey)))
  )
    return 'This shortcut is used by the browser or system. Choose another shortcut.';
}

function conflictingAction(
  action: GradingAction,
  key: Hotkey,
  bindings: GradingBindings,
  event?: KeyboardEvent
) {
  return gradingActionIds.find((id) => {
    const other = gradingKey(id, bindings);
    return (
      id !== action &&
      other !== null &&
      (areHotkeysEqual(other, key) ||
        (event !== undefined && matchesKeyboardEvent(event, other)))
    );
  });
}

export function shortcutError(
  action: GradingAction,
  key: Hotkey | null,
  bindings: GradingBindings,
  event?: KeyboardEvent
) {
  if (key === null) return undefined;
  const error = keyError(key, event);
  if (error) return error;
  const conflict = conflictingAction(action, key, bindings, event);
  return conflict
    ? `Already used for ${gradingActions[conflict].label.toLowerCase()}. Change or remove that shortcut first.`
    : undefined;
}

export function parseGradingBindings(
  value: string
): GradingBindings | undefined {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    return undefined;
  const bindings: GradingBindings = {};
  for (const action of gradingActionIds) {
    if (!(action in parsed)) continue;
    const key: unknown = Reflect.get(parsed, action);
    if (key === null) bindings[action] = null;
    else if (typeof key === 'string' && !keyError(key)) {
      bindings[action] = normalizeHotkey(key);
    } else return undefined;
  }
  if (
    gradingActionIds.some((action) => {
      const key = gradingKey(action, bindings);
      return key !== null && conflictingAction(action, key, bindings);
    })
  )
    return undefined;
  return bindings;
}
