import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from 'react';
import { Popover } from '@base-ui/react/popover';
import {
  CheckCheck,
  CircleAlert,
  CircleDot,
  ListTree,
  Text,
  Layers,
} from 'lucide-react';
import type { OutlineEntry } from '@/lib/assignment-outline';
import { buttonVariants } from '@/components/ui/button';

export type OutlineProps = {
  entries: OutlineEntry[];
  activeKey: string | null;
  errorKey?: string | null;
  outdated: boolean;
  disabled: boolean;
  editorId: string;
  showVersionHeadings?: boolean;
  onVersionSelect?: (version: string) => void;
  canSelect?: (entry: OutlineEntry) => boolean;
  onSelect: (entry: OutlineEntry) => void;
};

type Variant = 'popover' | 'rail';

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

function versionGroups(entries: OutlineEntry[]) {
  return entries.reduce<
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
}

function OutlineNav({
  ref,
  variant,
  className,
  entries,
  activeKey,
  errorKey,
  outdated,
  disabled,
  editorId,
  showVersionHeadings = false,
  onVersionSelect,
  canSelect = (entry) => entry.line !== null,
  onChoose,
}: Omit<OutlineProps, 'onSelect'> & {
  ref?: Ref<HTMLElement>;
  variant: Variant;
  className: string;
  onChoose: (target: OutlineEntry | string) => void;
}) {
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const tabStop =
    entries.find((entry) => entry.key === focusedKey)?.key ??
    entries.find((entry) => entry.key === activeKey)?.key ??
    entries[0]?.key;
  const groups = versionGroups(entries);
  const showVersions =
    showVersionHeadings && groups.some((group) => group.label);
  const headingBackground =
    variant === 'popover' ? 'bg-popover' : 'bg-background';

  return (
    <nav
      ref={ref}
      aria-label={
        variant === 'popover' ? 'Quick question navigation' : 'Questions'
      }
      className={className}
      onKeyDown={(event) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key))
          return;
        const buttons = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>(
            'button:not(:disabled)'
          )
        );
        const index = buttons.findIndex((button) => button === event.target);
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
      {groups.map((group) => (
        <div key={group.version}>
          {showVersions && (
            <h2
              className={`sticky top-0 z-10 text-xs font-semibold ${headingBackground}`}
            >
              {onVersionSelect && group.label ? (
                <button
                  type="button"
                  tabIndex={0}
                  aria-label={`Version ${group.label}: Back to top`}
                  aria-controls={editorId}
                  className="flex min-h-8 w-full items-center justify-between gap-2 border-b px-2 py-1.5 text-start hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  onClick={() => onChoose(group.version)}
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
              aria-label={`${showVersions && entry.versionLabel ? `Version ${entry.versionLabel}, ` : ''}question ${entry.number}${entry.key === errorKey ? ', has an error' : ''}: ${entry.prompt}`}
              aria-controls={editorId}
              aria-current={entry.key === activeKey ? 'location' : undefined}
              disabled={disabled || outdated || !canSelect(entry)}
              className={
                variant === 'popover'
                  ? 'group flex min-h-8 w-full scroll-mt-8 items-start gap-3 rounded-md px-2 py-2 text-start text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-40 aria-current:bg-accent aria-current:text-foreground'
                  : 'group flex min-h-8 w-full scroll-mt-8 items-baseline gap-2 py-1.5 pe-2 text-start text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:-outline-offset-2 focus-visible:outline-none disabled:opacity-40 aria-current:bg-accent aria-current:text-foreground'
              }
              style={
                variant === 'rail'
                  ? { paddingInlineStart: `${0.5 + entry.depth * 0.75}rem` }
                  : undefined
              }
              onClick={() => onChoose(entry)}
            >
              {variant === 'popover' ? (
                <>
                  <span
                    aria-hidden="true"
                    className={`flex w-10 min-w-0 shrink-0 items-center gap-1 truncate py-1 text-xs tabular-nums group-hover:font-semibold group-aria-current:font-semibold group-aria-current:underline group-aria-current:decoration-2 group-aria-current:underline-offset-4 ${entry.key === errorKey ? 'text-destructive' : ''}`}
                  >
                    {entry.number}
                    {entry.key === errorKey && (
                      <CircleAlert className="size-3 shrink-0" />
                    )}
                  </span>
                  <span className="min-w-0 flex-1 text-foreground">
                    <EntryPreview entry={entry} />
                  </span>
                </>
              ) : (
                <>
                  <span
                    aria-hidden="true"
                    className="w-7 shrink-0 text-xs tabular-nums group-aria-current:font-semibold"
                  >
                    {entry.number}
                  </span>
                  <span className="line-clamp-2 min-w-0 flex-1 text-sm wrap-anywhere text-foreground">
                    {entry.prompt}
                  </span>
                  {entry.key === errorKey ? (
                    <CircleAlert
                      className="size-3.5 shrink-0 self-center text-destructive"
                      aria-hidden="true"
                    />
                  ) : (
                    <span className="shrink-0 text-xs tabular-nums">
                      {entry.points}
                      <span className="sr-only"> points</span>
                    </span>
                  )}
                </>
              )}
            </button>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function QAOutlinePopover(props: OutlineProps) {
  const { entries, activeKey, outdated, disabled, onSelect, onVersionSelect } =
    props;
  const [open, setOpen] = useState(false);
  const selected = useRef<OutlineEntry | string | null>(null);
  const list = useRef<HTMLElement>(null);
  const activeEntry =
    entries.find((entry) => entry.key === activeKey) ?? entries[0];
  const showVersions =
    !!props.showVersionHeadings && entries.some((entry) => entry.versionLabel);

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
              const target = selected.current;
              selected.current = null;
              if (target === null) return true;
              if (typeof target === 'string') onVersionSelect?.(target);
              else onSelect(target);
              return false;
            }}
          >
            <OutlineNav
              {...props}
              ref={attachList}
              variant="popover"
              className="custom-scrollbar max-h-[min(24rem,60svh,calc(var(--available-height)-5rem))] overflow-y-auto overscroll-contain"
              onChoose={(target) => {
                selected.current = target;
                setOpen(false);
              }}
            />
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function QAOutlineRail({
  footer,
  ...props
}: OutlineProps & { footer?: ReactNode }) {
  const { activeKey, entries, outdated, onSelect, onVersionSelect } = props;
  const list = useRef<HTMLElement>(null);
  useEffect(() => {
    const container = list.current;
    const target = container?.querySelector<HTMLElement>(
      '[aria-current="location"]'
    );
    if (!container || !target) return;
    const top = target.offsetTop;
    const bottom = top + target.offsetHeight;
    if (top < container.scrollTop) container.scrollTop = top - 32;
    else if (bottom > container.scrollTop + container.clientHeight)
      container.scrollTop = bottom - container.clientHeight;
  }, [activeKey]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h2 className="flex min-h-9 items-center px-2 text-xs font-semibold text-muted-foreground">
        Questions
      </h2>
      {outdated && (
        <p className="px-2 pb-2 text-xs text-muted-foreground">
          Fix the error above to jump between questions.
        </p>
      )}
      {entries.length ? (
        <OutlineNav
          {...props}
          ref={list}
          variant="rail"
          className="custom-scrollbar relative min-h-0 flex-1 overflow-y-auto overscroll-contain"
          onChoose={(target) => {
            if (typeof target === 'string') onVersionSelect?.(target);
            else onSelect(target);
          }}
        />
      ) : (
        <p className="flex-1 px-2 text-sm text-muted-foreground">
          No questions yet.
        </p>
      )}
      {footer && <div className="shrink-0 border-t pt-2">{footer}</div>}
    </div>
  );
}
