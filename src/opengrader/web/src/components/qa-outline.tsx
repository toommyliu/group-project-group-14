import { useCallback, useEffect, useRef, useState } from 'react';
import { Popover } from '@base-ui/react/popover';
import { Tooltip } from '@base-ui/react/tooltip';
import {
  CheckCheck,
  ChevronDown,
  ChevronUp,
  CircleDot,
  ListTree,
  Text,
  Layers,
} from 'lucide-react';
import { useWideAssignmentLayout } from '@/hooks/use-wide-assignment-layout';
import type { OutlineEntry } from '@/lib/assignment-outline';

type OutlineProps = {
  entries: OutlineEntry[];
  activeKey: string | null;
  outdated: boolean;
  disabled: boolean;
  editorId: string;
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
  onSelect,
}: OutlineProps) {
  const wide = useWideAssignmentLayout();
  const [open, setOpen] = useState(false);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const selected = useRef<OutlineEntry | null>(null);
  const list = useRef<HTMLElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  function expand() {
    clearTimeout(closeTimer.current);
    setFocusedKey(null);
    setOpen(true);
  }
  function collapseSoon() {
    if (!wide) return;
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => {
      if (
        !popup.current?.contains(document.activeElement) ||
        !document.activeElement?.matches(':focus-visible')
      )
        setOpen(false);
    }, 150);
  }
  const [scroll, setScroll] = useState({ up: false, down: false });
  const tabStop =
    entries.find((entry) => entry.key === focusedKey)?.key ??
    entries.find((entry) => entry.key === activeKey)?.key ??
    entries[0]?.key;
  const activeEntry =
    entries.find((entry) => entry.key === activeKey) ?? entries[0];

  const updateScroll = useCallback(() => {
    const element = list.current;
    if (!element) return;
    const up = element.scrollTop > 1;
    const down =
      element.scrollTop + element.clientHeight < element.scrollHeight - 1;
    setScroll((previous) =>
      previous.up === up && previous.down === down ? previous : { up, down }
    );
  }, []);
  const attachList = useCallback(
    (element: HTMLElement | null) => {
      list.current = element;
      if (!element) return;
      const target = element.querySelector('[aria-current="location"]');
      target?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      const observer = new ResizeObserver(updateScroll);
      observer.observe(element);
      return () => observer.disconnect();
    },
    [updateScroll]
  );

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        onPointerEnter={(event) => {
          if (!wide || event.pointerType !== 'mouse' || disabled || outdated)
            return;
          setHoveredKey(activeEntry?.key ?? null);
          expand();
        }}
        onPointerLeave={collapseSoon}
        render={<button type="button" />}
        disabled={disabled || outdated}
        aria-label="Outline"
        className="flex min-h-11 items-center justify-center px-2.5 py-3 text-xs text-muted-foreground select-none hover:text-foreground focus-visible:-outline-offset-2 disabled:opacity-40 min-[65rem]:w-9 min-[65rem]:px-0"
      >
        <span
          aria-hidden="true"
          className={`flex min-w-0 flex-1 items-center gap-2 min-[65rem]:flex-col min-[65rem]:gap-1 min-[65rem]:px-1.5 ${open ? 'min-[65rem]:invisible' : ''}`}
        >
          <ListTree className="size-4" strokeWidth={1.5} />
          <span className="min-[65rem]:hidden">Outline</span>
          <span className="hidden max-w-full truncate text-xs font-medium tabular-nums min-[65rem]:block">
            {activeEntry?.number}
          </span>
        </span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner
          side={wide ? 'right' : 'bottom'}
          align={wide ? 'center' : 'end'}
          sideOffset={wide ? -36 : 8}
          className="z-40"
        >
          <Popover.Popup
            ref={popup}
            onPointerEnter={() => clearTimeout(closeTimer.current)}
            onPointerLeave={collapseSoon}
            aria-label="Question navigation"
            className="w-72 max-w-[calc(100vw-2rem)] rounded-xl bg-popover p-2 shadow-lg ring-1 ring-border select-none min-[65rem]:w-auto min-[65rem]:rounded-none min-[65rem]:bg-transparent min-[65rem]:px-0 min-[65rem]:py-1 min-[65rem]:shadow-none min-[65rem]:ring-0"
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
              if (!entry) return true;
              onSelect(entry);
              return false;
            }}
          >
            {wide && (scroll.up || scroll.down) && (
              <button
                type="button"
                aria-label="Scroll to earlier questions"
                disabled={!scroll.up}
                className="flex h-8 w-full items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-25"
                onClick={() =>
                  list.current?.scrollBy({
                    top: -list.current.clientHeight * 0.75,
                    behavior: 'instant',
                  })
                }
              >
                <ChevronUp className="size-4" aria-hidden="true" />
              </button>
            )}
            <nav
              ref={attachList}
              aria-label="Quick question navigation"
              className={`max-h-[min(16rem,40svh,calc(var(--available-height)-5rem))] overflow-y-auto overscroll-contain ${wide ? '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden' : 'custom-scrollbar'}`}
              onScroll={updateScroll}
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
              <Tooltip.Provider delay={0}>
                {entries.map((entry) => (
                  <Tooltip.Root
                    key={entry.key}
                    open={hoveredKey === entry.key}
                    onOpenChange={(next) =>
                      setHoveredKey((current) =>
                        next
                          ? entry.key
                          : current === entry.key
                            ? null
                            : current
                      )
                    }
                  >
                    <Tooltip.Trigger
                      render={<button type="button" />}
                      tabIndex={entry.key === tabStop ? 0 : -1}
                      data-outline-key={entry.key}
                      onPointerEnter={() => setHoveredKey(entry.key)}
                      onFocus={() => {
                        setFocusedKey(entry.key);
                        setHoveredKey(entry.key);
                      }}
                      aria-label={`Question ${entry.number}: ${entry.prompt}`}
                      aria-controls={editorId}
                      aria-current={
                        entry.key === activeKey ? 'location' : undefined
                      }
                      disabled={disabled || outdated || entry.line === null}
                      className="group flex min-h-8 w-full items-start gap-3 px-2 py-2 text-start text-muted-foreground hover:text-foreground focus-visible:-outline-offset-2 disabled:opacity-40 aria-current:text-foreground min-[65rem]:w-9 min-[65rem]:items-center min-[65rem]:gap-0 min-[65rem]:px-1.5 min-[65rem]:py-0"
                      onClick={() => {
                        selected.current = entry;
                        setOpen(false);
                      }}
                    >
                      <span
                        aria-hidden="true"
                        className="w-10 min-w-0 shrink-0 truncate py-1 text-xs tabular-nums group-hover:font-semibold group-aria-current:font-semibold group-aria-current:underline group-aria-current:decoration-2 group-aria-current:underline-offset-4 min-[65rem]:w-auto min-[65rem]:flex-1 min-[65rem]:text-center"
                      >
                        {entry.number}
                      </span>
                      <span className="min-w-0 flex-1 text-foreground min-[65rem]:hidden">
                        <EntryPreview entry={entry} />
                      </span>
                    </Tooltip.Trigger>
                    <Tooltip.Portal>
                      <Tooltip.Positioner
                        side="right"
                        align="center"
                        sideOffset={10}
                        className="z-50"
                      >
                        <Tooltip.Popup
                          onPointerEnter={() =>
                            clearTimeout(closeTimer.current)
                          }
                          onPointerLeave={collapseSoon}
                          className="custom-scrollbar hidden max-h-(--available-height) w-72 max-w-[calc(100vw-2rem)] overflow-y-auto overscroll-contain rounded-xl bg-popover p-4 text-popover-foreground shadow-lg ring-1 ring-border min-[65rem]:block"
                        >
                          <span className="mb-2 block text-sm font-semibold">
                            {entry.versionLabel && (
                              <span
                                className="mb-1 line-clamp-2 text-xs font-normal wrap-anywhere text-muted-foreground"
                                title={`Version ${entry.versionLabel}`}
                              >
                                Version {entry.versionLabel}
                              </span>
                            )}
                            <span className="block wrap-anywhere">
                              Question {entry.number}
                            </span>
                          </span>
                          <EntryPreview entry={entry} />
                        </Tooltip.Popup>
                      </Tooltip.Positioner>
                    </Tooltip.Portal>
                  </Tooltip.Root>
                ))}
              </Tooltip.Provider>
            </nav>
            {wide && (scroll.up || scroll.down) && (
              <button
                type="button"
                aria-label="Scroll to later questions"
                disabled={!scroll.down}
                className="flex h-8 w-full items-center justify-center text-muted-foreground hover:text-foreground disabled:opacity-25"
                onClick={() =>
                  list.current?.scrollBy({
                    top: list.current.clientHeight * 0.75,
                    behavior: 'instant',
                  })
                }
              >
                <ChevronDown className="size-4" aria-hidden="true" />
              </button>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
