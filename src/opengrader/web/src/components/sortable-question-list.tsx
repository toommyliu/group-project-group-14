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
import { RestrictToElement } from '@dnd-kit/dom/modifiers';
import {
  Accessibility,
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

const restrictToQuestionList = RestrictToElement.configure({
  element: ({ source }) => source?.element?.closest('ul') ?? null,
});

const SortableQuestion = memo(function SortableQuestion({
  question,
  index,
  group,
  disabled,
  count,
  renderQuestion,
  virtualized,
  virtualIndex,
  measureElement,
}: {
  question: Question;
  index: number;
  group: string;
  disabled: boolean;
  count: number;
  renderQuestion: RenderQuestion;
  virtualized: boolean;
  virtualIndex?: number;
  measureElement?: (element: HTMLLIElement | null) => void;
}) {
  const { ref, handleRef, isDragging, isDropTarget } = useSortable({
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
      aria-posinset={index + 1}
      aria-setsize={count}
      style={
        virtualized
          ? {
              position: 'absolute',
              insetInline: 0,
              top: 0,
            }
          : undefined
      }
      className={`relative min-w-0 rounded-lg ${isDragging ? '[&>*]:opacity-0' : ''} ${isDropTarget ? 'after:pointer-events-none after:absolute after:inset-0 after:rounded-lg after:inset-ring-2 after:inset-ring-ring' : ''}`}
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
          aria-label={`Move question ${index + 1}: ${question.prompt}`}
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
  onVisibleQuestionChange?: (sourceLine: number) => void;
}) {
  'use no memo';

  const group = useId();
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
    directDomUpdates: virtualized,
    directDomUpdatesMode: 'transform',
    onChange: (instance) => {
      const index = instance.range?.startIndex;
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

  useLayoutEffect(() => {
    if (!virtualized) return;
    const list = listRef.current;
    const scrollElement = scrollElementRef.current;
    const offsetElement = offsetElementRef?.current;
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
    return () => {
      observer.disconnect();
    };
  }, [offsetElementRef, scrollElementRef, virtualized]);

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
        if (!container) {
          element.scrollIntoView({ block: 'start', behavior: 'instant' });
          return;
        }
        const offset =
          element.getBoundingClientRect().top -
          container.getBoundingClientRect().top +
          container.scrollTop -
          16;
        virtualizer.scrollToOffset(offset);
      },
    }),
    [questions, virtualized, virtualizer, scrollElementRef]
  );

  const virtualItems = virtualized ? virtualizer.getVirtualItems() : [];
  const renderedIndexes = virtualized
    ? virtualItems.map((item) => item.index)
    : questions.map((_, index) => index);
  const setListElement = useCallback(
    (element: HTMLUListElement | null) => {
      listRef.current = element;
      if (virtualized) virtualizer.containerRef(element);
    },
    [virtualized, virtualizer]
  );
  return (
    <DragDropProvider
      modifiers={[restrictToQuestionList]}
      plugins={(defaults) =>
        defaults.map((plugin) =>
          plugin === Accessibility ? accessibility : plugin
        )
      }
      onBeforeDragStart={() => {
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
        onDraggingChange(false);
        const handle = source?.handle;
        if (handle instanceof HTMLElement)
          requestAnimationFrame(() => handle.focus());
      }}
    >
      <ul
        ref={setListElement}
        className={
          virtualized ? 'relative w-full' : compact ? 'space-y-2' : 'space-y-6'
        }
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
              virtualized={virtualized}
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
