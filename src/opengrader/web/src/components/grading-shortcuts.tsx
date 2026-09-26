import { setPreference, usePreferences } from '@/stores/preferences';
import { useEffect, useEffectEvent, useState, type ReactNode } from 'react';
import {
  formatForDisplay,
  useHotkeyRecorder,
  type Hotkey,
} from '@tanstack/react-hotkeys';
import { Keyboard } from 'lucide-react';
import {
  useGradingHotkeys,
  useGradingShortcutHints,
} from '@/hooks/use-grading-hotkeys';
import {
  gradingActions,
  gradingKey,
  rubricActions,
  shortcutError,
  shortcutLabel,
  type GradingAction,
} from '@/lib/grading-shortcuts';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Kbd } from './ui/kbd';
import { Switch } from './ui/switch';
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
  PopoverTitle,
} from './ui/popover';

const rows: { label: string; actions: GradingAction[] }[] = [
  {
    label: 'Previous / next submission',
    actions: ['previousSubmission', 'nextSubmission'],
  },
  {
    label: 'Previous / next question',
    actions: ['previousQuestion', 'nextQuestion'],
  },
  { label: 'Next ungraded response', actions: ['nextUngraded'] },
  { label: 'Focus score', actions: ['focusScore'] },
  { label: 'Write feedback', actions: ['focusFeedback'] },
  { label: 'Save now', actions: ['save'] },
  { label: 'Save and next ungraded', actions: ['saveAndNext'] },
  { label: 'Clear grade', actions: ['clearGrade'] },
  { label: 'Zoom in / out', actions: ['zoomIn', 'zoomOut'] },
  { label: 'Fit image', actions: ['fitImage'] },
  { label: 'Rotate image', actions: ['rotateImage'] },
  { label: 'Show shortcuts', actions: ['showShortcuts'] },
];

function Unassigned() {
  return (
    <>
      <span aria-hidden="true" className="text-muted-foreground">
        —
      </span>
      <span className="sr-only">Not assigned</span>
    </>
  );
}

function Keys({ children }: { children: ReactNode[] }) {
  return (
    <dd className="flex min-h-6 flex-wrap items-center justify-end gap-1">
      {children.map((child, index) => (
        <span key={index} className="inline-flex items-center gap-1">
          {index > 0 && (
            <span aria-hidden="true" className="text-muted-foreground">
              /
            </span>
          )}
          {child}
        </span>
      ))}
    </dd>
  );
}

export function GradingShortcuts() {
  const enabled = usePreferences((state) => state.gradingShortcuts);
  const bindings = usePreferences((state) => state.gradingBindings);
  const { shortcut, shortcutAria } = useGradingShortcutHints();
  const [open, setOpen] = useState(false);
  const [customizing, setCustomizing] = useState(false);
  const [recording, setRecording] = useState<GradingAction | null>(null);
  const [error, setError] = useState<{
    action: GradingAction;
    message: string;
  } | null>(null);
  const [status, setStatus] = useState('');
  const recorder = useHotkeyRecorder({
    recordBy: 'key',
    validate: (hotkey, { event }) =>
      recording === null
        ? false
        : (shortcutError(recording, hotkey, bindings, event) ?? true),
    onRecord: (hotkey) => {
      if (recording !== null) assign(recording, hotkey);
    },
    onClear: () => {
      if (recording !== null) assign(recording, null);
    },
    onReject: ({ message }) => {
      if (recording !== null) setError({ action: recording, message });
    },
    onCancel: stopRecording,
  });
  useGradingHotkeys([
    { action: 'showShortcuts', callback: () => setOpen(true) },
  ]);

  function stopRecording() {
    recorder.stopRecording();
    setRecording(null);
    setError(null);
  }

  function assign(action: GradingAction, value: Hotkey | null) {
    setPreference('gradingBindings', { ...bindings, [action]: value });
    stopRecording();
    const label = gradingActions[action].label;
    setStatus(
      value === null
        ? `Shortcut removed for ${label.toLowerCase()}.`
        : `${label}: ${shortcutLabel(value)}.`
    );
  }

  const leaveRecording = useEffectEvent((event: KeyboardEvent) => {
    if (event.key === 'Tab') stopRecording();
  });

  useEffect(() => {
    if (recording === null) return;
    // Let Tab move focus before the document-level recorder captures it.
    window.addEventListener('keydown', leaveRecording, true);
    return () => window.removeEventListener('keydown', leaveRecording, true);
  }, [recording]);

  function key(action: GradingAction) {
    const hotkey = gradingKey(action, bindings);
    if (!customizing)
      return hotkey === null ? (
        <Unassigned />
      ) : (
        <Kbd>{shortcutLabel(hotkey)}</Kbd>
      );
    const label = gradingActions[action].label.toLowerCase();
    const active = recording === action;
    const invalid = error?.action === action;
    return (
      <button
        type="button"
        className="group inline-flex min-h-6 min-w-6 items-center justify-center outline-none"
        aria-label={`${active ? 'Recording' : 'Change'} shortcut for ${label}: ${shortcutLabel(hotkey) || 'Not assigned'}`}
        aria-pressed={active}
        aria-invalid={invalid}
        aria-describedby={active || invalid ? 'shortcut-message' : undefined}
        onClick={() => {
          setRecording(action);
          setError(null);
          recorder.startRecording();
        }}
        onBlur={stopRecording}
      >
        <Kbd className="ring-1 ring-border group-hover:bg-accent group-hover:text-accent-foreground group-focus-visible:ring-ring group-aria-invalid:ring-destructive group-aria-pressed:bg-accent group-aria-pressed:text-accent-foreground group-aria-pressed:ring-ring">
          {active ? (
            '…'
          ) : hotkey === null ? (
            <Unassigned />
          ) : (
            shortcutLabel(hotkey)
          )}
        </Kbd>
      </button>
    );
  }

  function message(actions: readonly GradingAction[]) {
    if (error && actions.includes(error.action))
      return (
        <dd
          id="shortcut-message"
          role="alert"
          className="col-span-2 text-destructive"
        >
          {error.message}
        </dd>
      );
    if (recording && actions.includes(recording))
      return (
        <dd id="shortcut-message" className="col-span-2 text-muted-foreground">
          Press the new keys. <Kbd>{formatForDisplay('Escape')}</Kbd> cancels,{' '}
          <Kbd>{formatForDisplay('Backspace')}</Kbd> removes.
        </dd>
      );
  }

  function row({ label, actions }: (typeof rows)[number]) {
    if (
      !customizing &&
      actions.every((action) => gradingKey(action, bindings) === null)
    )
      return null;
    return (
      <div key={label} className="contents">
        <dt className="text-muted-foreground">{label}</dt>
        <Keys>{actions.map(key)}</Keys>
        {message(actions)}
      </div>
    );
  }

  const defaultRubric = rubricActions.every(
    (action) => gradingKey(action, bindings) === gradingActions[action].key
  );

  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        stopRecording();
      }}
    >
      <PopoverTrigger
        render={<Button variant="outline" />}
        aria-keyshortcuts={shortcutAria('showShortcuts')}
      >
        <Keyboard aria-hidden="true" /> Shortcuts{' '}
        {shortcut('showShortcuts') && <Kbd>{shortcut('showShortcuts')}</Kbd>}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        collisionAvoidance={{ align: 'flip' }}
        className="w-96 overscroll-contain"
        style={{ maxHeight: 'min(var(--available-height), 36rem)' }}
      >
        <div className="mb-1 flex items-center justify-between gap-2">
          <PopoverTitle className="font-semibold">
            Keyboard shortcuts
          </PopoverTitle>
          <div className="flex gap-1">
            {customizing && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setPreference('gradingBindings', {});
                  stopRecording();
                  setStatus('Default shortcuts restored.');
                }}
              >
                Reset
              </Button>
            )}
            <Button
              variant={customizing ? 'outline' : 'ghost'}
              size="sm"
              onClick={() => {
                setCustomizing(!customizing);
                stopRecording();
              }}
            >
              {customizing ? 'Done' : 'Customize'}
            </Button>
          </div>
        </div>
        <Label className="mb-3 grid min-h-8 grid-cols-[minmax(0,1fr)_auto] gap-x-6 text-sm leading-normal">
          <span>Enable grading shortcuts</span>
          <Switch
            checked={enabled}
            onCheckedChange={(checked) =>
              setPreference('gradingShortcuts', checked)
            }
          />
        </Label>
        <p role="status" className="sr-only">
          {status}
        </p>
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 text-xs">
          {rows.slice(0, 3).map(row)}
          {!customizing && defaultRubric ? (
            <div className="contents">
              <dt className="text-muted-foreground">Rubric items 1–10</dt>
              <Keys>{[<Kbd key="1">1–9</Kbd>, <Kbd key="0">0</Kbd>]}</Keys>
            </div>
          ) : (
            <div className="col-span-2 grid gap-1.5">
              <dt className="text-muted-foreground">Rubric items 1–10</dt>
              <dd className="flex flex-wrap gap-1">
                {rubricActions.map((action, index) => (
                  <span
                    key={action}
                    className="inline-flex min-h-6 min-w-6 items-center justify-center"
                  >
                    {!customizing && (
                      <span className="sr-only">Rubric item {index + 1}: </span>
                    )}
                    {key(action)}
                  </span>
                ))}
              </dd>
              {message(rubricActions)}
            </div>
          )}
          {rows.slice(3).map(row)}
        </dl>
      </PopoverContent>
    </Popover>
  );
}
