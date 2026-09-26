import { useId, useState } from 'react';
import { DragDropProvider } from '@dnd-kit/react';
import {
  Accessibility,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/dom';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import { GripVertical, Plus, X } from 'lucide-react';
import type { QuestionEdit } from '@/api/queries';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

type RubricItem = QuestionEdit['rubric'][number];
type DraftItem = Omit<RubricItem, 'subitems'> & {
  id: string;
  subitems: DraftItem[];
};
type Bounds = { min: number; max?: number };

const criterionAccessibility = Accessibility.configure({
  announcements: {
    dragstart: ({ operation: { source } }: DragStartEvent) =>
      source
        ? `Picked up ${source.handle?.getAttribute('aria-label')?.replace(/^Move /, '') ?? 'criterion'}. Use arrow keys to reorder, then Space to drop.`
        : undefined,
    dragover: () => undefined,
    dragend: ({ canceled }: DragEndEvent) =>
      canceled ? 'Move canceled.' : 'Move completed.',
  },
});

function draftItem(item: RubricItem): DraftItem {
  return {
    ...item,
    id: crypto.randomUUID(),
    subitems: (item.subitems ?? []).map(draftItem),
  };
}

function syncItems(rubric: RubricItem[], current: DraftItem[]): DraftItem[] {
  return rubric.map((item, index) => ({
    ...item,
    id: current[index]?.id ?? crypto.randomUUID(),
    subitems: syncItems(item.subitems ?? [], current[index]?.subitems ?? []),
  }));
}

function rubricValue({ id: _id, subitems, ...item }: DraftItem): RubricItem {
  return { ...item, subitems: subitems.map(rubricValue) };
}

function defaultRubric(points: number): DraftItem[] {
  return [
    { label: 'Correct', points_delta: 0 },
    { label: 'Partially Correct', points_delta: -points / 2 },
    { label: 'Incorrect', points_delta: -points },
  ].map(draftItem);
}

const round = (value: number) => Math.round(value * 100) / 100;
const total = (items: DraftItem[]) =>
  round(items.reduce((sum, item) => sum + (item.points_delta ?? 0), 0));
const signed = (value: number) => `${value > 0 ? '+' : ''}${value}`;

function PointsInput({
  label,
  value,
  bounds,
  bonus,
  disabled,
  onChange,
}: {
  label: string;
  value: number | null | undefined;
  bounds: Bounds;
  bonus: boolean;
  disabled: boolean;
  onChange: (value: number | null) => void;
}) {
  const [error, setError] = useState('');
  const errorId = useId();
  return (
    <div className="flex shrink-0 flex-col items-end gap-1">
      <Label className="flex items-center gap-1.5 text-sm leading-normal">
        <span className="sr-only">{label} points</span>
        <Input
          className="w-20 text-end tabular-nums"
          type="number"
          step="any"
          min={bounds.min}
          max={bounds.max}
          aria-invalid={!!error}
          aria-describedby={error ? errorId : undefined}
          value={value ?? ''}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.valueAsNumber;
            if (
              event.target.value &&
              (!Number.isFinite(next) ||
                next < bounds.min ||
                (bounds.max !== undefined && next > bounds.max))
            ) {
              setError(
                bonus
                  ? 'Bonus points must be zero or greater.'
                  : `Use a value from ${bounds.min} to ${bounds.max}.`
              );
              return;
            }
            setError('');
            onChange(Number.isFinite(next) ? next : null);
          }}
          onBlur={() => setError('')}
        />
        <span aria-hidden="true" className="text-muted-foreground">
          pts
        </span>
      </Label>
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function FeedbackInput({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  disabled: boolean;
  onChange: (value: string | null) => void;
}) {
  return (
    <Label className="block text-sm leading-normal">
      <span className="sr-only">Feedback for {label}</span>
      <Textarea
        value={value ?? ''}
        disabled={disabled}
        placeholder="Feedback shown with this score (optional)"
        className="min-h-8"
        onChange={(event) => onChange(event.target.value || null)}
      />
    </Label>
  );
}

function Criterion({
  item,
  index,
  count,
  group,
  bounds,
  bonus,
  disabled,
  onChange,
  onRemove,
}: {
  item: DraftItem;
  index: number;
  count: number;
  group: string;
  bounds: Bounds;
  bonus: boolean;
  disabled: boolean;
  onChange: (item: DraftItem) => void;
  onRemove: () => void;
}) {
  const { ref, handleRef, isDragging, isDropTarget } = useSortable({
    id: item.id,
    index,
    group,
    type: group,
    accept: group,
    disabled: disabled || count < 2,
  });
  return (
    <li
      ref={ref}
      data-rubric-index={index}
      className={`space-y-2 bg-background py-2 ${isDragging ? 'opacity-60' : ''} ${isDropTarget ? 'ring-2 ring-ring' : ''}`}
    >
      <div className="flex items-start gap-2">
        <Button
          ref={handleRef}
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled || count < 2}
          className="shrink-0 cursor-grab touch-none active:cursor-grabbing"
          aria-label={`Move ${item.label}`}
        >
          <GripVertical aria-hidden="true" />
        </Button>
        <Label className="block min-w-0 flex-1 text-sm leading-normal">
          <span className="sr-only">Criterion {index + 1}</span>
          <Input
            value={item.label}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...item, label: event.target.value })
            }
          />
        </Label>
        <PointsInput
          label={item.label}
          value={item.points_delta}
          bounds={bounds}
          bonus={bonus}
          disabled={disabled}
          onChange={(points_delta) => onChange({ ...item, points_delta })}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled}
          aria-label={`Remove ${item.label}`}
          title="Remove criterion"
          onClick={onRemove}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      <div className="ps-10">
        <FeedbackInput
          label={item.label}
          value={item.default_comment}
          disabled={disabled}
          onChange={(default_comment) => onChange({ ...item, default_comment })}
        />
      </div>
    </li>
  );
}

function CriteriaList({
  items,
  bounds,
  bonus,
  disabled,
  onChange,
}: {
  items: DraftItem[];
  bounds: Bounds;
  bonus: boolean;
  disabled: boolean;
  onChange: (items: DraftItem[]) => void;
}) {
  const group = useId();
  const [announcement, setAnnouncement] = useState('');
  return (
    <DragDropProvider
      plugins={(defaults) =>
        defaults.map((plugin) =>
          plugin === Accessibility ? criterionAccessibility : plugin
        )
      }
      onDragEnd={(event) => {
        const source = event.operation.source;
        if (
          !disabled &&
          !event.canceled &&
          isSortable(source) &&
          source.initialIndex !== source.index
        ) {
          const next = [...items];
          const [item] = next.splice(source.initialIndex, 1);
          next.splice(source.index, 0, item);
          onChange(next);
          setAnnouncement(
            `Moved ${item.label} to position ${source.index + 1} of ${items.length}.`
          );
        }
        const handle = source?.handle;
        if (handle instanceof HTMLElement)
          requestAnimationFrame(() => handle.focus());
      }}
    >
      <ul data-rubric-list className="divide-y border-s ps-2">
        {items.map((item, index) => (
          <Criterion
            key={item.id}
            item={item}
            index={index}
            count={items.length}
            group={group}
            bounds={bounds}
            bonus={bonus}
            disabled={disabled}
            onChange={(updated) =>
              onChange(
                items.map((value) => (value.id === item.id ? updated : value))
              )
            }
            onRemove={() =>
              onChange(items.filter((value) => value.id !== item.id))
            }
          />
        ))}
      </ul>
      <span role="status" className="sr-only">
        {announcement}
      </span>
    </DragDropProvider>
  );
}

function RubricLevel({
  item,
  index,
  points,
  positive,
  disabled,
  onChange,
  onRemove,
}: {
  item: DraftItem;
  index: number;
  points: number;
  positive: boolean;
  disabled: boolean;
  onChange: (item: DraftItem) => void;
  onRemove?: () => void;
}) {
  const bonus = item.label === 'Bonus';
  const fixed = item.label === 'Correct' || item.label === 'Incorrect';
  const bounds: Bounds = bonus
    ? { min: 0 }
    : positive
      ? { min: 0, max: points }
      : { min: -points, max: 0 };
  const criteria = item.subitems;
  const criteriaTotal = total(criteria);
  const target = positive ? points : -points;
  const canSplit = bonus || item.label === 'Partially Correct';
  function changeCriteria(subitems: DraftItem[]) {
    onChange({
      ...item,
      points_delta: subitems.length ? null : criteriaTotal,
      subitems,
    });
  }
  return (
    <li data-rubric-index={index} className="space-y-2 p-3">
      <div className="flex min-h-8 flex-wrap items-center gap-2">
        <h5 className="min-w-0 flex-1 font-medium">{item.label}</h5>
        {criteria.length ? (
          <span className="text-muted-foreground tabular-nums">
            {signed(criteriaTotal)} pts total
          </span>
        ) : fixed ? (
          <span
            className="text-muted-foreground tabular-nums"
            title="Set by the question's points and scoring method"
          >
            {signed(item.points_delta ?? 0)} pts
          </span>
        ) : (
          <PointsInput
            label={item.label}
            value={item.points_delta}
            bounds={bounds}
            bonus={bonus}
            disabled={disabled}
            onChange={(points_delta) => onChange({ ...item, points_delta })}
          />
        )}
        {onRemove && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={disabled}
            aria-label={`Remove ${item.label}`}
            title={`Remove ${item.label.toLowerCase()}`}
            onClick={onRemove}
          >
            <X aria-hidden="true" />
          </Button>
        )}
      </div>
      <FeedbackInput
        label={item.label}
        value={item.default_comment}
        disabled={disabled}
        onChange={(default_comment) => onChange({ ...item, default_comment })}
      />
      {!!item.notes?.length && (
        <p className="text-sm whitespace-pre-wrap text-muted-foreground">
          {item.notes.join('\n')}
        </p>
      )}
      {!!criteria.length && (
        <CriteriaList
          items={criteria}
          bounds={bounds}
          bonus={bonus}
          disabled={disabled}
          onChange={changeCriteria}
        />
      )}
      {item.label === 'Partially Correct' &&
        !!criteria.length &&
        criteriaTotal !== target && (
          <p className="text-sm text-destructive">
            Criteria must add up to {signed(target)} pts. They add up to{' '}
            {signed(criteriaTotal)} now.
          </p>
        )}
      {canSplit && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => {
            const subitems = criteria.length
              ? criteria
              : [
                  draftItem({
                    label: 'Criterion 1',
                    points_delta: item.points_delta ?? 0,
                    default_comment: item.default_comment,
                  }),
                ];
            onChange({
              ...item,
              points_delta: null,
              subitems: [
                ...subitems,
                draftItem({
                  label: `Criterion ${subitems.length + 1}`,
                  points_delta: 0,
                }),
              ],
            });
          }}
        >
          <Plus aria-hidden="true" />
          {criteria.length ? 'Add criterion' : 'Split into criteria'}
        </Button>
      )}
    </li>
  );
}

export function RubricEditor({
  rubric,
  points,
  disabled,
  onChange,
}: {
  rubric: RubricItem[];
  points: number;
  disabled: boolean;
  onChange: (rubric: RubricItem[]) => void;
}) {
  const [items, setItems] = useState(() => rubric.map(draftItem));
  const [receivedRubric, setReceivedRubric] = useState(rubric);
  const headingId = useId();
  if (rubric !== receivedRubric) {
    setReceivedRubric(rubric);
    // Retain drag identities and input focus when the question rescales points.
    setItems(syncItems(rubric, items));
  }
  function update(next: DraftItem[]) {
    setItems(next);
    onChange(next.map(rubricValue));
  }
  const values = items
    .filter((item) => item.label !== 'Bonus')
    .flatMap((item) =>
      item.subitems.length
        ? item.subitems.map((child) => child.points_delta ?? 0)
        : [item.points_delta ?? 0]
    );
  const positive =
    !values.some((value) => value < 0) && values.some((value) => value > 0);

  function setScoring(award: boolean) {
    if (award === positive) return;
    update(
      items.map((item) => {
        if (item.label === 'Bonus') return item;
        if (item.label === 'Correct')
          return { ...item, points_delta: award ? points : 0 };
        if (item.label === 'Incorrect')
          return { ...item, points_delta: award ? 0 : -points };
        if (item.subitems.length)
          return {
            ...item,
            points_delta: null,
            subitems: item.subitems.map((child) => ({
              ...child,
              points_delta: -(child.points_delta ?? 0),
            })),
          };
        return {
          ...item,
          points_delta: (item.points_delta ?? 0) + (award ? points : -points),
        };
      })
    );
  }

  if (!items.length)
    return (
      <section
        aria-labelledby={headingId}
        className="flex flex-wrap items-center gap-x-4 gap-y-2"
      >
        <div className="min-w-0 flex-1">
          <h4 id={headingId} className="font-medium">
            Rubric
          </h4>
          <p className="text-sm text-muted-foreground">
            Optional. Gives graders preset scores and feedback.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => update(defaultRubric(points))}
        >
          <Plus aria-hidden="true" />
          Add rubric
        </Button>
      </section>
    );

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h4 id={headingId} className="font-medium">
          Rubric
        </h4>
        <div
          role="group"
          aria-label="Scoring method"
          className="flex border border-border"
        >
          {[
            { award: false, label: `Deduct from ${points}` },
            { award: true, label: 'Award from 0' },
          ].map((option) => (
            <Button
              key={option.label}
              type="button"
              size="sm"
              variant={option.award === positive ? 'secondary' : 'ghost'}
              aria-pressed={option.award === positive}
              disabled={disabled}
              onClick={() => setScoring(option.award)}
            >
              {option.label}
            </Button>
          ))}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ms-auto"
          disabled={disabled}
          onClick={() => update([])}
        >
          Remove rubric
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        Scores stay between 0 and {points}. Changing the question&rsquo;s points
        rescales these values; bonus points stay fixed and are added last.
      </p>
      <ul data-rubric-list className="divide-y border">
        {items.map((item, index) => (
          <RubricLevel
            key={item.id}
            item={item}
            index={index}
            points={points}
            positive={positive}
            disabled={disabled}
            onChange={(updated) =>
              update(
                items.map((value) => (value.id === item.id ? updated : value))
              )
            }
            onRemove={
              item.label === 'Bonus'
                ? () => update(items.filter((value) => value.id !== item.id))
                : undefined
            }
          />
        ))}
      </ul>
      {!items.some((item) => item.label === 'Bonus') && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() =>
            update([
              ...items,
              draftItem({
                label: 'Bonus',
                subitems: [{ label: 'Bonus criterion', points_delta: 1 }],
              }),
            ])
          }
        >
          <Plus aria-hidden="true" />
          Add bonus
        </Button>
      )}
    </section>
  );
}
