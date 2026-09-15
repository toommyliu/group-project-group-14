import { useId, useState, type ReactNode } from 'react';
import { DragDropProvider, DragOverlay } from '@dnd-kit/react';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import { RestrictToElement } from '@dnd-kit/dom/modifiers';
import {
  Accessibility,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/dom';
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

function SortableQuestion({
  question,
  index,
  group,
  disabled,
  count,
  renderQuestion,
}: {
  question: Question;
  index: number;
  group: string;
  disabled: boolean;
  count: number;
  renderQuestion: RenderQuestion;
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
      ref={ref}
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
}

export function SortableQuestionList({
  questions,
  disabled,
  compact,
  labelPrefix = '',
  onDraggingChange,
  onMove,
  renderQuestion,
}: {
  questions: Question[];
  disabled: boolean;
  compact: boolean;
  labelPrefix?: string;
  onDraggingChange: (dragging: boolean) => void;
  onMove: (sourceLines: number[]) => void;
  renderQuestion: RenderQuestion;
}) {
  const group = useId();
  const [announcement, setAnnouncement] = useState('');
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
      <ul className={compact ? 'space-y-2' : 'space-y-6'}>
        {questions.map((question, index) => (
          <SortableQuestion
            key={question.source_line}
            question={question}
            index={index}
            group={group}
            disabled={disabled}
            count={questions.length}
            renderQuestion={renderQuestion}
          />
        ))}
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
