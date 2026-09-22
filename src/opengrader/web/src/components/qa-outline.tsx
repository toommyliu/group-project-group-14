import { useCallback, useRef, useState } from 'react';
import { Popover } from '@base-ui/react/popover';
import { CheckCheck, CircleDot, ListTree, Text, Layers } from 'lucide-react';
import type { OutlineEntry } from '@/lib/assignment-outline';
import { buttonVariants } from '@/components/ui/button';

type OutlineProps = {
  entries: OutlineEntry[];
  activeKey: string | null;
  outdated: boolean;
  disabled: boolean;
  editorId: string;
  showVersionHeadings?: boolean;
  onVersionSelect?: (version: string) => void;
  canSelect?: (entry: OutlineEntry) => boolean;
  onSelect: (entry: OutlineEntry) => void;
};

const icons = {
  'Question group': Layers,
  'Written answer': Text,
  'Multiple choice': CircleDot,
  'Multiple answer': CheckCheck,
};

function EntryPreview({ entry }: { entry: OutlineEntry }) {
  const Icon = icons[entry.kind];
  return (
    <>
      <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex min-w-0 items-center gap-2">
          <Icon
            className="size-3.5 shrink-0"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          <span>{entry.kind}</span>
        </span>
        <span className="max-w-full wrap-anywhere tabular-nums">
          {entry.points} pt
        </span>
      </span>
      <span className="mt-1 line-clamp-3 text-sm leading-relaxed wrap-anywhere">
        {entry.prompt}
      </span>
    </>
  );
}

/** Keep the current question at the edge and reveal numbered destinations on demand. */
export function QAOutlineRail({
  entries,
  activeKey,
  outdated,
  disabled,
  editorId,
  showVersionHeadings = false,
  onVersionSelect,
  canSelect = (entry) => entry.line !== null,
  onSelect,
}: OutlineProps) {
  const [open, setOpen] = useState(false);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const selected = useRef<OutlineEntry | string | null>(null);
  const list = useRef<HTMLElement>(null);
  const tabStop =
    entries.find((entry) => entry.key === focusedKey)?.key ??
    entries.find((entry) => entry.key === activeKey)?.key ??
    entries[0]?.key;
  const activeEntry =
    entries.find((entry) => entry.key === activeKey) ?? entries[0];
  const versionGroups = entries.reduce<
    { version: string; label: string; entries: OutlineEntry[] }[]
  >((groups, entry) => {
    const last = groups.at(-1);
    if (last?.version === entry.version) last.entries.push(entry);
    else
      groups.push({
        version: entry.version,
        label: entry.versionLabel,
        entries: [entry],
      });
    return groups;
  }, []);
  const showVersions =
    showVersionHeadings && versionGroups.some((group) => group.label);

  const attachList = useCallback((element: HTMLElement | null) => {
    list.current = element;
    if (!element) return;
    const target = element.querySelector('[aria-current="location"]');
    target?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }, []);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        render={<button type="button" />}
        disabled={disabled || outdated}
        aria-label={
          activeEntry
            ? `Outline, ${showVersions ? `version ${activeEntry.versionLabel}, ` : ''}question ${activeEntry.number}`
            : 'Outline'
        }
        className={buttonVariants({ variant: 'outline' })}
      >
        <ListTree className="size-4" strokeWidth={1.5} aria-hidden="true" />
        <span>Outline</span>
        <span>
          {showVersions && activeEntry?.versionLabel
            ? `${activeEntry.versionLabel} · `
            : ''}
          {activeEntry?.number}
        </span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner
          side="bottom"
          align="end"
          sideOffset={8}
          className="z-40"
        >
          <Popover.Popup
            aria-label="Question navigation"
            className="w-72 max-w-[calc(100vw-2rem)] bg-popover p-2 shadow-lg ring-1 ring-border select-none"
            initialFocus={(type) =>
              type === 'keyboard'
                ? (list.current?.querySelector<HTMLElement>(
                    'button[tabindex="0"]'
                  ) ?? true)
                : false
            }
            finalFocus={() => {
              const entry = selected.current;
              selected.current = null;
              if (entry === null) return true;
              if (typeof entry === 'string') onVersionSelect?.(entry);
              else onSelect(entry);
              return false;
            }}
          >
            <nav
              ref={attachList}
              aria-label="Quick question navigation"
              className="custom-scrollbar max-h-[min(24rem,60svh,calc(var(--available-height)-5rem))] overflow-y-auto overscroll-contain"
              onKeyDown={(event) => {
                if (
                  !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)
                )
                  return;
                const buttons = Array.from(
                  event.currentTarget.querySelectorAll<HTMLButtonElement>(
                    'button:not(:disabled)'
                  )
                );
                const index = buttons.findIndex(
                  (button) => button === event.target
                );
                if (index < 0) return;
                event.preventDefault();
                const next =
                  event.key === 'Home'
                    ? 0
                    : event.key === 'End'
                      ? buttons.length - 1
                      : (index +
                          (event.key === 'ArrowDown' ? 1 : -1) +
                          buttons.length) %
                        buttons.length;
                buttons[next]?.focus({ preventScroll: true });
                buttons[next]?.scrollIntoView({
                  block: 'nearest',
                  behavior: 'instant',
                });
              }}
            >
              {versionGroups.map((group) => (
                <div key={group.version}>
                  {showVersions && (
                    <h2 className="sticky top-0 z-10 bg-popover text-xs font-semibold">
                      {onVersionSelect && group.label ? (
                        <button
                          type="button"
                          tabIndex={0}
                          aria-label={`Version ${group.label}: Back to top`}
                          aria-controls={editorId}
                          className="flex min-h-8 w-full items-center justify-between gap-2 border-b px-2 py-1.5 text-start hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                          onClick={() => {
                            selected.current = group.version;
                            setOpen(false);
                          }}
                        >
                          <span>Version {group.label}</span>
                        </button>
                      ) : (
                        <span className="block border-b px-2 py-1.5">
                          {group.label ? `Version ${group.label}` : 'Questions'}
                        </span>
                      )}
                    </h2>
                  )}
                  {group.entries.map((entry) => (
                    <button
                      key={entry.key}
                      type="button"
                      tabIndex={entry.key === tabStop ? 0 : -1}
                      data-outline-key={entry.key}
                      onFocus={() => setFocusedKey(entry.key)}
                      aria-label={`${showVersions && entry.versionLabel ? `Version ${entry.versionLabel}, ` : ''}question ${entry.number}: ${entry.prompt}`}
                      aria-controls={editorId}
                      aria-current={
                        entry.key === activeKey ? 'location' : undefined
                      }
                      disabled={disabled || outdated || !canSelect(entry)}
                      className="group flex min-h-8 w-full scroll-mt-8 items-start gap-3 rounded-md px-2 py-2 text-start text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-40 aria-current:bg-accent aria-current:text-foreground"
                      onClick={() => {
                        selected.current = entry;
                        setOpen(false);
                      }}
                    >
                      <span
                        aria-hidden="true"
                        className="w-10 min-w-0 shrink-0 truncate py-1 text-xs tabular-nums group-hover:font-semibold group-aria-current:font-semibold group-aria-current:underline group-aria-current:decoration-2 group-aria-current:underline-offset-4"
                      >
                        {entry.number}
                      </span>
                      <span className="min-w-0 flex-1 text-foreground">
                        <EntryPreview entry={entry} />
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </nav>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
