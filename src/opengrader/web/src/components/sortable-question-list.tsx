import {
  memo,
  useCallback,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type Ref,
  type RefObject,
} from 'react';
import { DragDropProvider, DragOverlay } from '@dnd-kit/react';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import { configurator, Modifier, type DragOperation } from '@dnd-kit/abstract';
import {
  Accessibility,
  type DragDropManager,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/dom';
import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
} from '@tanstack/react-virtual';
import { GripVertical } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { Button } from './ui/button';
import {
  compactQuestionRowClassName,
  QuestionRowHeading,
} from './question-row-heading';

type Question = QAPreview['document']['questions'][number];
type RenderQuestion = (
  question: Question,
  index: number,
  handle: ReactNode
) => ReactNode;

export type QuestionListNavigation = {
  scrollTo: (sourceLine: number) => boolean;
  scrollToElement: (element: HTMLElement) => void;
};

const accessibility = Accessibility.configure({
  announcements: {
    dragstart: ({ operation: { source } }: DragStartEvent) =>
      source
        ? `${source.handle?.getAttribute('aria-label')}. Use arrow keys to reorder, then Space to drop.`
        : undefined,
    dragover: () => undefined,
    dragend: ({ canceled }: DragEndEvent) =>
      canceled ? 'Move canceled.' : 'Move completed.',
  },
});

type Grab = {
  top: number | null;
  list: HTMLElement | null;
  scroller: HTMLElement | null;
};

class FollowPointer extends Modifier<DragDropManager, { grab: Grab }> {
  static configure = configurator(FollowPointer);

  apply({ shape, transform }: DragOperation) {
    const grab = this.options?.grab;
    if (!shape || !grab || grab.top === null) return { x: 0, y: transform.y };
    const start = shape.initial.boundingRectangle.top;
    const top = grab.top + transform.y;
    if (!grab.list) return { x: 0, y: top - start };
    const list = grab.list.getBoundingClientRect();
    const view = grab.scroller?.getBoundingClientRect();
    const min = Math.max(list.top, view?.top ?? -Infinity);
    const max =
      Math.min(list.bottom, view?.bottom ?? Infinity) -
      shape.current.boundingRectangle.height;
    return { x: 0, y: Math.max(min, Math.min(top, max)) - start };
  }
}

function scrollParent(element: Element) {
  for (let node = element.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === 'auto' || overflowY === 'scroll') return node;
  }
  return null;
}

const SortableQuestion = memo(function SortableQuestion({
  question,
  index,
  group,
  disabled,
  count,
  renderQuestion,
  virtualIndex,
  measureElement,
}: {
  question: Question;
  index: number;
  group: string;
  disabled: boolean;
  count: number;
  renderQuestion: RenderQuestion;
  virtualIndex?: number;
  measureElement?: (element: HTMLLIElement | null) => void;
}) {
  const { ref, handleRef, isDragging } = useSortable({
    id: question.source_line!,
    index,
    group,
    type: group,
    accept: group,
    disabled: disabled || count < 2,
  });
  return (
    <li
      ref={(element) => {
        ref(element);
        measureElement?.(element);
      }}
      data-index={virtualIndex}
      data-question-line={question.source_line}
      aria-posinset={index + 1}
      aria-setsize={count}
      className={`relative min-w-0 rounded-lg ${isDragging ? 'bg-muted/60 outline-2 -outline-offset-2 outline-border outline-dashed [&>*]:invisible' : ''}`}
    >
      {renderQuestion(
        question,
        index,
        <Button
          ref={handleRef}
          type="button"
          variant="ghost"
          size="icon"
          className="h-10 shrink-0 cursor-grab touch-none focus-visible:-outline-offset-2 active:cursor-grabbing"
          disabled={disabled || count < 2}
          data-question-handle
          aria-label={`Move question ${index + 1}: ${question.prompt}`}
          title="Drag to reorder"
        >
          <GripVertical aria-hidden="true" />
        </Button>
      )}
    </li>
  );
});

export function SortableQuestionList({
  ref,
  questions,
  disabled,
  compact,
  labelPrefix = '',
  onDraggingChange,
  onMove,
  renderQuestion,
  scrollElementRef,
  offsetElementRef,
  stickyElementRef,
  onVisibleQuestionChange,
}: {
  ref?: Ref<QuestionListNavigation>;
  questions: Question[];
  disabled: boolean;
  compact: boolean;
  labelPrefix?: string;
  onDraggingChange: (dragging: boolean) => void;
  onMove: (sourceLines: number[]) => void;
  renderQuestion: RenderQuestion;
  scrollElementRef?: RefObject<HTMLElement | null>;
  offsetElementRef?: RefObject<HTMLElement | null>;
  stickyElementRef?: RefObject<HTMLElement | null>;
  onVisibleQuestionChange?: (sourceLine: number) => void;
}) {
  'use no memo';

  const group = useId();
  const [grab] = useState<Grab>(() => ({
    top: null,
    list: null,
    scroller: null,
  }));
  const [modifiers] = useState(() => [FollowPointer.configure({ grab })]);
  const [announcement, setAnnouncement] = useState('');
  const listRef = useRef<HTMLUListElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const lastVisibleIndex = useRef(-1);
  const visibleQuestionCallback = useRef(onVisibleQuestionChange);
  if (visibleQuestionCallback.current !== onVisibleQuestionChange) {
    visibleQuestionCallback.current = onVisibleQuestionChange;
    lastVisibleIndex.current = -1;
  }
  const virtualized = !!scrollElementRef;
  const estimatedSize = compact ? 64 : 560;
  const getItemKey = useCallback(
    (index: number) => questions[index]?.source_line ?? index,
    [questions]
  );
  const rangeExtractor = useCallback(
    (range: Range) =>
      compact
        ? Array.from({ length: questions.length }, (_, index) => index)
        : defaultRangeExtractor(range),
    [compact, questions.length]
  );
  // TanStack Virtual exposes an imperative instance; this component opts out above.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: questions.length,
    enabled: virtualized,
    getScrollElement: () => scrollElementRef?.current ?? null,
    getItemKey,
    estimateSize: () => estimatedSize,
    gap: compact ? 8 : 24,
    overscan: 2,
    rangeExtractor,
    scrollMargin,
    onChange: (instance) => {
      const scroller = instance.scrollElement;
      const atBottom =
        !!scroller &&
        scroller.scrollTop > 0 &&
        scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 1;
      const index = atBottom
        ? questions.length - 1
        : instance.range?.startIndex;
      if (index === undefined || index === lastVisibleIndex.current) return;
      lastVisibleIndex.current = index;
      const line = questions[index]?.source_line;
      if (line != null) visibleQuestionCallback.current?.(line);
    },
  });

  const measuredCompact = useRef(compact);
  useLayoutEffect(() => {
    if (virtualized && measuredCompact.current !== compact)
      virtualizer.measure();
    measuredCompact.current = compact;
  }, [compact, virtualized, virtualizer]);

  const anchor = useRef<{
    line: number;
    top: number;
    compact: boolean;
  } | null>(null);
  useLayoutEffect(() => {
    const target = anchor.current;
    if (!target || target.compact !== compact) return;
    anchor.current = null;
    const find = () =>
      listRef.current?.querySelector<HTMLElement>(
        `:scope > [data-question-line="${target.line}"]`
      );
    if (compact) {
      const element = find();
      const scroller = element && scrollParent(element);
      if (element && scroller)
        scroller.scrollTop += element.getBoundingClientRect().top - target.top;
      return;
    }
    const index = questions.findIndex(
      (question) => question.source_line === target.line
    );
    if (virtualized && index >= 0)
      virtualizer.scrollToIndex(index, { align: 'start' });
    const frame = requestAnimationFrame(() => {
      const element = find();
      if (!element) return;
      const container = scrollElementRef?.current;
      if (container)
        virtualizer.scrollToOffset(
          element.getBoundingClientRect().top -
            container.getBoundingClientRect().top +
            container.scrollTop -
            (stickyElementRef?.current?.offsetHeight ?? 0) -
            16
        );
      else element.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      element
        .querySelector<HTMLElement>('[data-question-handle]')
        ?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  });

  useLayoutEffect(() => {
    if (!virtualized) return;
    const list = listRef.current;
    const scrollElement = scrollElementRef.current;
    const offsetElement = offsetElementRef?.current;
    const stickyElement = stickyElementRef?.current;
    if (!list || !scrollElement) return;
    const observedList = list;
    const scrollContainer = scrollElement;
    function measureMargin() {
      const next =
        observedList.getBoundingClientRect().top -
        scrollContainer.getBoundingClientRect().top +
        scrollContainer.scrollTop;
      setScrollMargin((current) =>
        Math.abs(current - next) < 0.5 ? current : next
      );
    }
    measureMargin();
    const observer = new ResizeObserver(measureMargin);
    if (offsetElement) observer.observe(offsetElement);
    if (stickyElement) observer.observe(stickyElement);
    return () => {
      observer.disconnect();
    };
  }, [offsetElementRef, stickyElementRef, scrollElementRef, virtualized]);

  useImperativeHandle(
    ref,
    () => ({
      scrollTo(sourceLine) {
        function includesLine(question: Question): boolean {
          return (
            question.source_line === sourceLine ||
            !!question.subquestions?.some(includesLine)
          );
        }
        const index = questions.findIndex(includesLine);
        if (index < 0) return false;
        if (virtualized)
          virtualizer.scrollToIndex(index, {
            align: 'start',
            behavior: 'auto',
          });
        return true;
      },
      scrollToElement(element) {
        const container = scrollElementRef?.current;
        const sticky = stickyElementRef?.current;
        if (!container) {
          element.scrollIntoView({ block: 'start', behavior: 'instant' });
          return;
        }
        const offset =
          element.getBoundingClientRect().top -
          container.getBoundingClientRect().top +
          container.scrollTop -
          (sticky?.offsetHeight ?? 0) -
          16;
        virtualizer.scrollToOffset(offset);
      },
    }),
    [questions, virtualized, virtualizer, scrollElementRef, stickyElementRef]
  );

  const virtualItems = virtualized ? virtualizer.getVirtualItems() : [];
  const renderedIndexes = virtualized
    ? virtualItems.map((item) => item.index)
    : questions.map((_, index) => index);
  const first = virtualItems[0];
  const last = virtualItems.at(-1);
  const spacers =
    first && last
      ? {
          paddingTop: first.start - scrollMargin,
          paddingBottom: Math.max(
            0,
            virtualizer.getTotalSize() - (last.end - scrollMargin)
          ),
        }
      : undefined;
  return (
    <DragDropProvider
      modifiers={modifiers}
      plugins={(defaults) =>
        defaults.map((plugin) =>
          plugin === Accessibility ? accessibility : plugin
        )
      }
      onBeforeDragStart={(event) => {
        const element = event.operation.source?.element;
        const line = event.operation.source?.id;
        grab.top = element?.getBoundingClientRect().top ?? null;
        grab.list = listRef.current;
        grab.scroller = element ? scrollParent(element) : null;
        if (element && typeof line === 'number')
          anchor.current = {
            line,
            top: element.getBoundingClientRect().top,
            compact: true,
          };
        // The provider waits for this render before measuring drag geometry.
        onDraggingChange(true);
      }}
      onDragEnd={(event) => {
        const source = event.operation.source;
        if (
          !disabled &&
          !event.canceled &&
          isSortable(source) &&
          source.initialIndex !== source.index
        ) {
          const next = questions.map((question) => question.source_line!);
          const [moved] = next.splice(source.initialIndex, 1);
          next.splice(source.index, 0, moved);
          onMove(next);
          setAnnouncement(
            `Moved question ${source.initialIndex + 1} to position ${source.index + 1} of ${questions.length}.`
          );
        }
        Object.assign(grab, { top: null, list: null, scroller: null });
        if (typeof source?.id === 'number')
          anchor.current = { line: source.id, top: 0, compact: false };
        onDraggingChange(false);
      }}
    >
      <ul
        ref={listRef}
        style={spacers}
        className={`flex w-full flex-col ${compact ? 'gap-2' : 'gap-6'}`}
      >
        {renderedIndexes.map((index) => {
          const question = questions[index];
          if (!question) return null;
          return (
            <SortableQuestion
              key={question.source_line}
              question={question}
              index={index}
              group={group}
              disabled={disabled}
              count={questions.length}
              renderQuestion={renderQuestion}
              virtualIndex={virtualized ? index : undefined}
              measureElement={
                virtualized ? virtualizer.measureElement : undefined
              }
            />
          );
        })}
      </ul>
      <DragOverlay
        dropAnimation={null}
        style={{ height: 'auto', maxHeight: 'none' }}
      >
        {(source) => {
          const index = questions.findIndex(
            (question) => question.source_line === source.id
          );
          const question = questions[index];
          if (!question) return null;
          return (
            <div
              className={`${compactQuestionRowClassName} flex text-foreground shadow-lg`}
              aria-hidden="true"
            >
              <QuestionRowHeading
                label={`Question ${labelPrefix}${index + 1}`}
                prompt={question.prompt}
                handle={
                  <span className="flex h-10 w-8 shrink-0 items-center justify-center">
                    <GripVertical className="size-4" />
                  </span>
                }
              />
            </div>
          );
        }}
      </DragOverlay>
      <span role="status" className="sr-only">
        {announcement}
      </span>
    </DragDropProvider>
  );
}
