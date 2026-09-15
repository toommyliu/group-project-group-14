import { type Ref, useEffect, useId, useRef, useState } from 'react';
import { DragDropProvider } from '@dnd-kit/react';
import { PointerActivationConstraints, PointerSensor } from '@dnd-kit/dom';
import { RestrictToElement } from '@dnd-kit/dom/modifiers';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import {
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Pencil,
  Play,
  Plus,
  Square,
  X,
} from 'lucide-react';
import type { TerminalSession } from '@/api/terminals';
import { Button } from './ui/button';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuTrigger,
} from './ui/context-menu';
import { Input } from './ui/input';
import { TabsList, TabsTrigger } from './ui/tabs';

// Let touch movement scroll; a short hold still enables deliberate touch reordering.
const pointerSensor = PointerSensor.configure({
  activationConstraints: (event) =>
    event.pointerType === 'touch'
      ? [new PointerActivationConstraints.Delay({ value: 250, tolerance: 5 })]
      : [new PointerActivationConstraints.Distance({ value: 5 })],
});

class RestrictToHorizontalAxis extends RestrictToElement {
  override apply(operation: Parameters<RestrictToElement['apply']>[0]) {
    return { ...operation.transform, y: 0 };
  }
}

const restrictToTabScroller = RestrictToElement.configure({
  element: ({ source }) =>
    source?.element?.closest('[data-terminal-tabs-scroll]') ?? null,
});

function TerminalName({
  title,
  disabled,
  onSave,
  onCancel,
}: {
  title: string;
  disabled: boolean;
  onSave: (title: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(title);
  const saving = useRef(false);
  async function save() {
    if (saving.current || disabled) return;
    const name = draft.trim();
    if (!name || name === title) {
      onCancel();
      return;
    }
    saving.current = true;
    try {
      await onSave(name);
    } catch {
      // The page owns the visible mutation error; keep the input open for correction.
    } finally {
      saving.current = false;
    }
  }
  return (
    <form
      className="absolute inset-y-1 start-8 end-8 z-20"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <Input
        autoFocus
        aria-label="Terminal name"
        maxLength={80}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={(event) => event.currentTarget.select()}
        onBlur={() => {
          void save();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            saving.current = true;
            onCancel();
          }
        }}
        className="h-full border-ring bg-background px-1 text-xs font-medium ring-1 ring-ring/50"
      />
    </form>
  );
}

function TerminalTab({
  session,
  index,
  count,
  group,
  disabled,
  editing,
  onRename,
  onSave,
  onCancel,
  onMove,
  onStart,
  onStop,
  onClose,
}: {
  session: TerminalSession;
  index: number;
  count: number;
  group: string;
  disabled: boolean;
  editing: boolean;
  onRename: (id: string) => void;
  onSave: (title: string) => Promise<void>;
  onCancel: () => void;
  onMove: (offset: number) => void;
  onStart: () => void;
  onStop: () => void;
  onClose: () => void;
}) {
  const [contextMenuOpen, setContextMenuOpen] = useState(false);
  const { ref, handleRef, isDragging, isDropTarget } = useSortable({
    id: session.id,
    index,
    group,
    type: group,
    accept: group,
    disabled: disabled || editing,
    transition: null,
  });
  const failed =
    session.status === 'stopped' &&
    session.message.startsWith('Could not start:');
  const status = failed
    ? 'Failed to start'
    : session.status === 'running'
      ? 'Running'
      : 'Stopped';
  const Icon = failed
    ? CircleAlert
    : session.status === 'running'
      ? CircleDot
      : Square;
  return (
    <ContextMenu open={contextMenuOpen} onOpenChange={setContextMenuOpen}>
      <ContextMenuTrigger
        ref={ref}
        role="presentation"
        className={`group/tab relative flex h-full shrink-0 items-center border-e border-border/70 has-[.peer:focus-visible]:outline-2 has-[.peer:focus-visible]:-outline-offset-2 has-[.peer:focus-visible]:outline-foreground has-[button[data-active]]:z-10 has-[button[data-active]]:bg-background has-[button[data-active]]:ring-1 has-[button[data-active]]:ring-border has-[button[data-active]]:ring-inset ${isDragging ? 'z-20 opacity-60' : ''} ${isDropTarget ? 'outline-2 -outline-offset-2 outline-ring' : ''}`}
      >
        <TabsTrigger
          ref={handleRef}
          value={session.id}
          title={`${session.title} · ${status}`}
          aria-label={`${session.title}, ${status}`}
          aria-keyshortcuts="F2 Alt+ArrowLeft Alt+ArrowRight"
          className="peer flex h-full! max-w-56 min-w-24 cursor-grab touch-pan-x items-center gap-2 border-0 bg-transparent px-3 pe-1 transition-colors focus-visible:border-transparent! focus-visible:ring-0! focus-visible:outline-none! active:cursor-grabbing data-active:border-transparent data-active:bg-transparent dark:data-active:border-transparent dark:data-active:bg-transparent"
          onDoubleClick={() => {
            if (!disabled) onRename(session.id);
          }}
          onKeyDown={(event) => {
            if (disabled) return;
            if (event.key === 'F2') {
              event.preventDefault();
              onRename(session.id);
            }
            if (
              event.altKey &&
              (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
            ) {
              event.preventDefault();
              event.stopPropagation();
              onMove(event.key === 'ArrowLeft' ? -1 : 1);
            }
          }}
        >
          <Icon
            className={`size-3 shrink-0 ${failed ? 'text-destructive' : ''}`}
            aria-hidden="true"
          />
          <span className={`truncate ${editing ? 'invisible' : ''}`}>
            {session.title}
          </span>
        </TabsTrigger>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="me-1 opacity-100 transition-opacity focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/tab:opacity-100 [@media(hover:hover)]:peer-data-active:opacity-100"
          disabled={disabled}
          aria-label={`Close ${session.title}`}
          title={`Close ${session.title}`}
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </Button>
        {editing && (
          <TerminalName
            title={session.title}
            disabled={disabled}
            onSave={onSave}
            onCancel={onCancel}
          />
        )}
      </ContextMenuTrigger>
      <ContextMenuContent
        onKeyDownCapture={(event) => {
          if (event.key !== 'F2' || disabled) return;
          event.preventDefault();
          event.stopPropagation();
          setContextMenuOpen(false);
          requestAnimationFrame(() => onRename(session.id));
        }}
      >
        {session.status === 'running' ? (
          <ContextMenuItem disabled={disabled} onClick={onStop}>
            <Square aria-hidden="true" />
            Stop session
          </ContextMenuItem>
        ) : (
          <ContextMenuItem disabled={disabled} onClick={onStart}>
            <Play aria-hidden="true" />
            Start session
          </ContextMenuItem>
        )}
        <ContextMenuSeparator />
        <ContextMenuItem
          disabled={disabled || editing}
          onClick={() => onRename(session.id)}
        >
          <Pencil aria-hidden="true" />
          Rename
          <ContextMenuShortcut>F2</ContextMenuShortcut>
        </ContextMenuItem>
        <ContextMenuItem
          disabled={disabled || editing || index === 0}
          onClick={() => onMove(-1)}
        >
          <ArrowLeft aria-hidden="true" />
          Move left
        </ContextMenuItem>
        <ContextMenuItem
          disabled={disabled || editing || index === count - 1}
          onClick={() => onMove(1)}
        >
          <ArrowRight aria-hidden="true" />
          Move right
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          variant="destructive"
          disabled={disabled}
          onClick={onClose}
        >
          <X aria-hidden="true" />
          Close terminal
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}

export function TerminalTabs({
  sessions,
  disabled,
  renamingId,
  onMove,
  onRename,
  onSave,
  onCancel,
  onStart,
  onStop,
  onClose,
  createDisabled,
  createButtonRef,
  onCreate,
}: {
  sessions: TerminalSession[];
  disabled: boolean;
  renamingId: string | null;
  onMove: (ids: string[]) => void;
  onRename: (id: string) => void;
  onSave: (id: string, title: string) => Promise<void>;
  onCancel: () => void;
  onStart: (session: TerminalSession) => void;
  onStop: (session: TerminalSession) => void;
  onClose: (session: TerminalSession) => void;
  createDisabled: boolean;
  createButtonRef: Ref<HTMLButtonElement>;
  onCreate: () => void;
}) {
  const group = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const [announcement, setAnnouncement] = useState('');
  const [overflow, setOverflow] = useState({ start: false, end: false });
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const update = () => {
      const next = {
        start: list.scrollLeft > 1,
        end: list.scrollLeft + list.clientWidth < list.scrollWidth - 1,
      };
      setOverflow((current) =>
        current.start === next.start && current.end === next.end
          ? current
          : next
      );
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(list);
    list.addEventListener('scroll', update, { passive: true });
    return () => {
      observer.disconnect();
      list.removeEventListener('scroll', update);
    };
  }, [sessions, renamingId]);

  function scroll(direction: -1 | 1) {
    const list = listRef.current;
    if (!list) return;
    list.scrollBy({ left: direction * Math.max(120, list.clientWidth * 0.6) });
  }
  function move(from: number, to: number) {
    if (disabled || from === to || to < 0 || to >= sessions.length) return;
    const ids = sessions.map((session) => session.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    onMove(ids);
    setAnnouncement(
      `Moving ${sessions[from].title} to position ${to + 1} of ${sessions.length}.`
    );
  }
  return (
    <DragDropProvider
      sensors={[pointerSensor]}
      modifiers={[RestrictToHorizontalAxis, restrictToTabScroller]}
      onDragEnd={(event) => {
        const source = event.operation.source;
        if (!event.canceled && isSortable(source))
          move(source.initialIndex, source.index);
        const handle = source?.handle;
        if (handle instanceof HTMLElement)
          requestAnimationFrame(() => handle.focus());
      }}
    >
      <div
        data-terminal-tabs
        className="flex h-11 shrink-0 overflow-hidden border-b bg-muted/50"
      >
        <div
          data-terminal-tabs-scroll
          className="relative min-w-0 flex-1 overflow-hidden"
        >
          <TabsList
            ref={listRef}
            aria-label="Terminals"
            aria-describedby={`${group}-help`}
            className="h-full! w-full touch-pan-x [scrollbar-width:none] [scrollbar-gutter:auto] justify-start overflow-x-auto overflow-y-hidden overscroll-x-contain bg-transparent p-0 [&::-webkit-scrollbar]:hidden"
            activateOnFocus={false}
          >
            {sessions.map((session, index) => (
              <TerminalTab
                key={session.id}
                session={session}
                index={index}
                count={sessions.length}
                group={group}
                disabled={disabled}
                editing={renamingId === session.id}
                onRename={onRename}
                onSave={(title) => onSave(session.id, title)}
                onCancel={onCancel}
                onMove={(offset) => move(index, index + offset)}
                onStart={() => onStart(session)}
                onStop={() => onStop(session)}
                onClose={() => onClose(session)}
              />
            ))}
          </TabsList>
          {overflow.start && (
            <div className="pointer-events-none absolute inset-y-0 start-0 z-30 flex w-12 items-center bg-linear-to-r from-muted via-muted/90 to-transparent ps-1">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="pointer-events-auto bg-muted/80"
                aria-label="Show earlier terminals"
                onClick={() => scroll(-1)}
              >
                <ChevronLeft aria-hidden="true" />
              </Button>
            </div>
          )}
          {overflow.end && (
            <div className="pointer-events-none absolute inset-y-0 end-0 z-30 flex w-12 items-center justify-end bg-linear-to-l from-muted via-muted/90 to-transparent pe-1">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="pointer-events-auto bg-muted/80"
                aria-label="Show later terminals"
                onClick={() => scroll(1)}
              >
                <ChevronRight aria-hidden="true" />
              </Button>
            </div>
          )}
        </div>
        <Button
          ref={createButtonRef}
          type="button"
          variant="ghost"
          size="icon"
          className="h-full! w-11! shrink-0 border-0 border-s border-border focus-visible:-outline-offset-2"
          disabled={createDisabled}
          aria-label="New terminal"
          title="New terminal"
          onClick={onCreate}
        >
          <Plus aria-hidden="true" />
        </Button>
      </div>
      <span id={`${group}-help`} className="sr-only">
        Drag tabs or use Alt+Left and Alt+Right to reorder. Press F2 to rename.
      </span>
      <span role="status" className="sr-only">
        {announcement}
      </span>
    </DragDropProvider>
  );
}
