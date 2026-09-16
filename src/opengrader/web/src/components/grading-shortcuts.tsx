import { usePreference } from '@/hooks/use-preference';
import { useState } from 'react';
import { formatForDisplay, useHotkey } from '@tanstack/react-hotkeys';
import { Keyboard } from 'lucide-react';
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

function ShortcutKeys({
  keys,
  accessibleLabel,
}: {
  keys: string[];
  accessibleLabel: string;
}) {
  return (
    <dd className="flex min-h-5 items-center justify-end whitespace-nowrap">
      <span aria-hidden="true" className="flex items-center gap-0.5">
        {keys.map((key, index) =>
          key === '/' ? (
            <span
              key={`${key}-${index}`}
              className="inline-flex h-4 items-center text-muted-foreground"
            >
              /
            </span>
          ) : (
            <Kbd
              key={`${key}-${index}`}
              className="h-4 min-w-4 px-0.5 text-[0.625rem] tabular-nums"
            >
              {key}
            </Kbd>
          )
        )}
      </span>
      <span className="sr-only">{accessibleLabel}</span>
    </dd>
  );
}

export function GradingShortcuts() {
  const [enabled, setEnabled] = usePreference('gradingShortcuts');
  const [open, setOpen] = useState(false);
  useHotkey({ key: '/', shift: true }, () => setOpen((value) => !value), {
    enabled,
  });
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline" />}>
        <Keyboard aria-hidden="true" /> Shortcuts <Kbd>?</Kbd>
      </PopoverTrigger>
      <PopoverContent align="start" collisionAvoidance={{ align: 'flip' }}>
        <PopoverTitle className="mb-1 font-semibold">
          Keyboard shortcuts
        </PopoverTitle>
        <Label className="mb-2 grid min-h-8 grid-cols-[minmax(0,1fr)_auto] gap-x-6 text-sm leading-normal">
          <span>Enable grading shortcuts</span>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </Label>
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 text-xs">
          {[
            {
              keys: ['J', '/', 'K'],
              accessibleKeys: 'J or K',
              label: 'Next / previous submission',
            },
            {
              keys: ['N'],
              accessibleKeys: 'N',
              label: 'Next ungraded response',
            },
            {
              keys: ['[', '/', ']'],
              accessibleKeys: 'left bracket or right bracket',
              label: 'Previous / next question',
            },
            {
              keys: ['1–9'],
              accessibleKeys: '1 through 9',
              label: 'Select rubric item',
            },
            {
              keys: ['C'],
              accessibleKeys: 'C',
              label: 'Write feedback',
            },
            {
              keys: formatForDisplay('Mod+S').split(/\s+/),
              accessibleKeys: 'Command or Control and S',
              label: 'Save now',
            },
            {
              keys: formatForDisplay('Mod+Enter').split(/\s+/),
              accessibleKeys: 'Command or Control and Enter',
              label: 'Next ungraded response',
            },
            {
              keys: ['+', '/', '−', '/', '0'],
              accessibleKeys: 'plus, minus, or 0',
              label: 'Zoom in / out / fit image',
            },
            {
              keys: ['R'],
              accessibleKeys: 'R',
              label: 'Rotate image',
            },
          ].map(({ keys, accessibleKeys, label }) => (
            <div key={label + accessibleKeys} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <ShortcutKeys keys={keys} accessibleLabel={accessibleKeys} />
            </div>
          ))}
        </dl>
      </PopoverContent>
    </Popover>
  );
}
