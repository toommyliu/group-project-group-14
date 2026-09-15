import { useId, useState } from 'react';
import { Popover } from '@base-ui/react/popover';
import { DragDropProvider } from '@dnd-kit/react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Accessibility,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/dom';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import {
  ArrowDown,
  ArrowUp,
  GripVertical,
  Plus,
  Settings2,
  Trash2,
} from 'lucide-react';
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

const rubricAccessibility = Accessibility.configure({
  announcements: {
    dragstart: ({ operation: { source } }: DragStartEvent) =>
      source
        ? `Picked up ${source.handle?.getAttribute('aria-label')?.replace(/^Move /, '') ?? 'rubric item'}.`
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

function SortableItem({
  item,
  index,
  count,
  group,
  nested,
  points,
  positive,
  bonus,
  disabled,
  onChange,
  onMove,
  onRemove,
}: {
  item: DraftItem;
  index: number;
  count: number;
  group: string;
  nested: boolean;
  points: number;
  positive: boolean;
  bonus: boolean;
  disabled: boolean;
  onChange: (item: DraftItem) => void;
  onMove: (from: number, to: number) => void;
  onRemove?: () => void;
}) {
  const [pointsError, setPointsError] = useState('');
  const fullCredit =
    !nested &&
    (positive ? item.label === 'Correct' : item.label === 'Incorrect');
  const fixedZero =
    !nested &&
    (positive ? item.label === 'Incorrect' : item.label === 'Correct');
  const minimum = fixedZero
    ? 0
    : bonus || positive
      ? 0
      : fullCredit
        ? undefined
        : -points;
  const maximum = fixedZero
    ? 0
    : bonus
      ? undefined
      : positive
        ? fullCredit
          ? undefined
          : points
        : 0;
  const errorId = useId();
  const { ref, handleRef, isDragging, isDropTarget } = useSortable({
    id: item.id,
    index,
    group,
    type: group,
    accept: group,
    disabled,
  });
  return (
    <li
      ref={ref}
      className={`rounded-lg border bg-background p-3 ${isDragging ? 'opacity-60' : ''} ${isDropTarget ? 'ring-2 ring-ring' : ''}`}
    >
      <div className="flex items-start gap-2">
        <Button
          ref={handleRef}
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled || count < 2}
          className="h-10 shrink-0 cursor-grab touch-none active:cursor-grabbing"
          aria-label={`Move ${item.label}`}
        >
          <GripVertical aria-hidden="true" />
        </Button>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex min-h-10 flex-wrap items-center gap-3">
            {nested ? (
              <Label className="block min-w-0 flex-[1_1_10rem] text-sm leading-normal">
                <span className="sr-only">Criterion</span>
                <Input
                  value={item.label}
                  disabled={disabled}
                  onChange={(event) =>
                    onChange({ ...item, label: event.target.value })
                  }
                />
              </Label>
            ) : (
              <h4 className="min-w-0 flex-1 font-medium">{item.label}</h4>
            )}
            {!item.subitems.length && (
              <Label className="ms-auto flex shrink-0 items-center gap-2 text-sm leading-normal">
                <span className="sr-only">Points</span>
                <Input
                  className="w-20 tabular-nums"
                  type="number"
                  step={fullCredit ? 1 : 'any'}
                  min={minimum}
                  max={maximum}
                  readOnly={fixedZero}
                  aria-invalid={!!pointsError}
                  aria-describedby={pointsError ? errorId : undefined}
                  value={item.points_delta ?? ''}
                  disabled={disabled}
                  onChange={(event) => {
                    const value = event.target.valueAsNumber;
                    if (
                      event.target.value &&
                      (!Number.isFinite(value) ||
                        (minimum !== undefined && value < minimum) ||
                        (maximum !== undefined && value > maximum) ||
                        (fullCredit && !Number.isSafeInteger(value)))
                    ) {
                      setPointsError(
                        fullCredit
                          ? 'Use a whole number of points with the current scoring sign.'
                          : bonus
                            ? 'Bonus points must be zero or greater.'
                            : `Use a value from ${minimum} to ${maximum} points.`
                      );
                      return;
                    }
                    setPointsError('');
                    onChange({
                      ...item,
                      points_delta: Number.isFinite(value) ? value : null,
                    });
                  }}
                  onBlur={() => setPointsError('')}
                />
                <span aria-hidden="true" className="text-muted-foreground">
                  pts
                </span>
              </Label>
            )}
          </div>
          {pointsError && (
            <p id={errorId} role="alert" className="text-sm text-destructive">
              {pointsError}
            </p>
          )}
          <Label className="block space-y-1 text-sm leading-normal">
            <span>Feedback</span>
            <Textarea
              value={item.default_comment ?? ''}
              disabled={disabled}
              className="min-h-16"
              onChange={(event) =>
                onChange({
                  ...item,
                  default_comment: event.target.value || null,
                })
              }
            />
          </Label>
          {!!item.notes?.length && (
            <p className="text-sm whitespace-pre-wrap text-muted-foreground">
              {item.notes.join('\n')}
            </p>
          )}
          <div className="flex flex-wrap gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || index === 0}
              aria-label={`Move ${item.label} up`}
              onClick={() => onMove(index, index - 1)}
            >
              <ArrowUp aria-hidden="true" /> Up
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || index === count - 1}
              aria-label={`Move ${item.label} down`}
              onClick={() => onMove(index, index + 1)}
            >
              <ArrowDown aria-hidden="true" /> Down
            </Button>
            {onRemove && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={onRemove}
                aria-label={`Remove ${item.label}`}
              >
                <Trash2 aria-hidden="true" /> Remove
              </Button>
            )}
          </div>
          {!!item.subitems.length && (
            <RubricList
              items={item.subitems}
              nested
              points={points}
              positive={positive}
              bonus={bonus}
              disabled={disabled}
              onChange={(subitems) =>
                onChange({ ...item, points_delta: null, subitems })
              }
            />
          )}
          {(item.label === 'Partially Correct' || item.label === 'Bonus') &&
            !nested && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() => {
                  const subitems = item.subitems.length
                    ? item.subitems
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
                <Plus aria-hidden="true" /> Add criterion
              </Button>
            )}
        </div>
      </div>
    </li>
  );
}

function RubricList({
  items,
  nested = false,
  points,
  positive,
  bonus = false,
  disabled,
  onChange,
}: {
  items: DraftItem[];
  nested?: boolean;
  points: number;
  positive: boolean;
  bonus?: boolean;
  disabled: boolean;
  onChange: (items: DraftItem[]) => void;
}) {
  const group = useId();
  const [announcement, setAnnouncement] = useState('');
  function move(from: number, to: number) {
    if (disabled || from === to || to < 0 || to >= items.length) return;
    const next = [...items];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
    setAnnouncement(
      `Moved ${item.label} to position ${to + 1} of ${items.length}.`
    );
  }
  return (
    <DragDropProvider
      plugins={(defaults) =>
        defaults.map((plugin) =>
          plugin === Accessibility ? rubricAccessibility : plugin
        )
      }
      onDragEnd={(event) => {
        const handle = event.operation.source?.handle;
        if (!event.canceled && isSortable(event.operation.source)) {
          move(
            event.operation.source.initialIndex,
            event.operation.source.index
          );
        }
        if (handle instanceof HTMLElement)
          requestAnimationFrame(() => handle.focus());
      }}
    >
      <ul className="space-y-3">
        {items.map((item, index) => (
          <SortableItem
            key={item.id}
            item={item}
            index={index}
            count={items.length}
            group={group}
            nested={nested}
            points={points}
            positive={positive}
            bonus={bonus || (!nested && item.label === 'Bonus')}
            disabled={disabled}
            onMove={move}
            onChange={(updated) =>
              onChange(
                items.map((value) => (value.id === item.id ? updated : value))
              )
            }
            onRemove={
              nested || item.label === 'Bonus'
                ? () => onChange(items.filter((value) => value.id !== item.id))
                : undefined
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
  if (rubric !== receivedRubric) {
    setReceivedRubric(rubric);
    // Retain drag identities and input focus when the question rescales points.
    setItems(syncItems(rubric, items));
  }
  function update(next: DraftItem[]) {
    setItems(next);
    onChange(next.map(rubricValue));
  }
  const base = items.filter((item) => item.label !== 'Bonus');
  const values = base.flatMap((item) =>
    item.subitems.length
      ? item.subitems.map((child) => child.points_delta ?? 0)
      : [item.points_delta ?? 0]
  );
  const positive =
    !values.some((value) => value < 0) && values.some((value) => value > 0);
  return (
    <section className="space-y-4" aria-label="Rubric editor">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Rubric</h3>
        {!!items.length && (
          <Popover.Root>
            <Popover.Trigger
              render={<Button type="button" variant="ghost" size="sm" />}
              disabled={disabled}
            >
              <Settings2 aria-hidden="true" />
              Rubric settings
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Positioner
                side="bottom"
                align="end"
                sideOffset={8}
                className="z-50"
              >
                <Popover.Popup className="max-h-(--available-height) w-80 max-w-[calc(100vw-2rem)] overflow-y-auto overscroll-contain border bg-popover p-4 text-popover-foreground shadow-md">
                  <Popover.Title className="font-semibold">
                    Rubric settings
                  </Popover.Title>
                  <Label className="mt-3 block space-y-2 text-sm leading-normal">
                    <span>Scoring method</span>
                    <Select
                      items={[
                        {
                          value: 'negative',
                          label: `Subtract points, starting from ${points}`,
                        },
                        {
                          value: 'positive',
                          label: 'Add points, starting from 0',
                        },
                      ]}
                      value={positive ? 'positive' : 'negative'}
                      disabled={disabled}
                      onValueChange={(value) => {
                        if (
                          !value ||
                          value === (positive ? 'positive' : 'negative')
                        )
                          return;
                        const award = value === 'positive';
                        update(
                          items.map((item) => {
                            if (item.label === 'Bonus') return item;
                            if (item.label === 'Correct')
                              return {
                                ...item,
                                points_delta: award ? points : 0,
                              };
                            if (item.label === 'Incorrect')
                              return {
                                ...item,
                                points_delta: award ? 0 : -points,
                              };
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
                              points_delta:
                                (item.points_delta ?? 0) +
                                (award ? points : -points),
                            };
                          })
                        );
                      }}
                    >
                      <SelectTrigger
                        aria-label="Scoring method"
                        className="w-full"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="negative">
                          Subtract points, starting from {points}
                        </SelectItem>
                        <SelectItem value="positive">
                          Add points, starting from 0
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </Label>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Changing the method converts point values. Review criterion
                    descriptions to match awards or deductions.
                  </p>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Scores are limited to 0–{points}. Bonus points are added
                    afterward.
                  </p>
                </Popover.Popup>
              </Popover.Positioner>
            </Popover.Portal>
          </Popover.Root>
        )}
      </div>
      {items.length ? (
        <>
          <RubricList
            items={items}
            points={points}
            positive={positive}
            disabled={disabled}
            onChange={update}
          />
          <div className="flex flex-wrap items-center gap-2">
            {!items.some((item) => item.label === 'Bonus') && (
              <Button
                type="button"
                variant="outline"
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
                Add bonus criterion
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => update([])}
            >
              Remove rubric
            </Button>
          </div>
        </>
      ) : (
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={() => update(defaultRubric(points))}
        >
          <Plus aria-hidden="true" /> Add rubric
        </Button>
      )}
    </section>
  );
}
