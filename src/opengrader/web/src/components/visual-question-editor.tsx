import {
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { CircleAlert, Ellipsis, Plus, Trash2, X } from 'lucide-react';
import { rescaleRubric } from '@/lib/rubric-points';
import type { QuestionEdit } from '@/api/queries';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Textarea } from './ui/textarea';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { RubricEditor } from './rubric-editor';
import {
  compactQuestionRowClassName,
  QuestionRowHeading,
} from './question-row-heading';

const deferredQuestionStyle = {
  contentVisibility: 'auto',
  containIntrinsicSize: 'auto 32rem',
} satisfies CSSProperties;

const choiceLabels = Array.from('ABCDEFGHIJKLMNOPQRSTUVWXYZ');

function removeChoice(edit: QuestionEdit, index: number) {
  const choices = edit.choices ?? [];
  const correct = edit.correct_choice_labels ?? [];
  const kept = choices.filter((_, i) => i !== index);
  return {
    choices: kept.map((choice, i) => ({ ...choice, label: choiceLabels[i] })),
    correct_choice_labels: kept.flatMap((choice, i) =>
      correct.includes(choice.label) ? [choiceLabels[i]] : []
    ),
  };
}

function PointsField({
  edit,
  disabled,
  onChange,
}: {
  edit: QuestionEdit;
  disabled: boolean;
  onChange: (change: Partial<QuestionEdit>) => void;
}) {
  const [input, setInput] = useState(String(edit.points));
  const baseline = useRef({ points: edit.points, rubric: edit.rubric });
  return (
    <Label className="flex shrink-0 items-center gap-1.5 text-sm leading-normal">
      <span className="sr-only">Points</span>
      <Input
        type="number"
        min="0"
        step="1"
        required
        className="w-16 text-end tabular-nums"
        value={input}
        disabled={disabled}
        onFocus={() => {
          baseline.current = { points: edit.points, rubric: edit.rubric };
        }}
        onChange={(event) => {
          const value = event.target.value;
          setInput(value);
          const points = Number(value);
          if (
            value.trim() &&
            Number.isSafeInteger(points) &&
            points >= 0 &&
            points !== edit.points
          )
            onChange({
              points,
              rubric: rescaleRubric(
                baseline.current.rubric,
                baseline.current.points,
                points
              ),
            });
        }}
        onBlur={() => setInput(String(edit.points))}
      />
      <span aria-hidden="true" className="text-muted-foreground">
        pts
      </span>
    </Label>
  );
}

function AnswerFields({
  edit,
  disabled,
  onChange,
}: {
  edit: QuestionEdit;
  disabled: boolean;
  onChange: (change: Partial<QuestionEdit>) => void;
}) {
  const headingId = useId();
  const choices = edit.choices ?? [];
  const correct = edit.correct_choice_labels ?? [];
  const multipleChoice = !!choices.length;
  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h4 id={headingId} className="font-medium">
          Answer
        </h4>
        <div
          role="group"
          aria-label="Answer type"
          className="flex border border-border"
        >
          {[
            { choices: false, label: 'Written' },
            { choices: true, label: 'Multiple choice' },
          ].map((option) => (
            <Button
              key={option.label}
              type="button"
              size="sm"
              variant={
                option.choices === multipleChoice ? 'secondary' : 'ghost'
              }
              aria-pressed={option.choices === multipleChoice}
              disabled={disabled}
              onClick={() => {
                if (option.choices === multipleChoice) return;
                onChange(
                  option.choices
                    ? {
                        choices: [
                          { label: 'A', text: 'First choice' },
                          { label: 'B', text: 'Second choice' },
                        ],
                        correct_choice_labels: ['A'],
                        answers: [],
                      }
                    : {
                        choices: [],
                        correct_choice_labels: [],
                        answers: [''],
                      }
                );
              }}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>
      {multipleChoice ? (
        <>
          <ul className="space-y-2">
            {choices.map((choice, index) => (
              <li key={choice.label} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="w-5 shrink-0 text-center font-medium"
                >
                  {choice.label}
                </span>
                <Label className="block min-w-0 flex-1 leading-normal">
                  <span className="sr-only">Choice {choice.label}</span>
                  <Input
                    value={choice.text}
                    disabled={disabled}
                    placeholder={`Choice ${choice.label}`}
                    onChange={(event) =>
                      onChange({
                        choices: choices.map((value, i) =>
                          i === index
                            ? { ...value, text: event.target.value }
                            : value
                        ),
                      })
                    }
                  />
                </Label>
                <Label className="flex min-h-8 shrink-0 items-center gap-1.5 px-1 text-sm leading-normal">
                  <Checkbox
                    checked={correct.includes(choice.label)}
                    disabled={disabled}
                    aria-label={`Choice ${choice.label} is correct`}
                    onCheckedChange={(checked) =>
                      onChange({
                        correct_choice_labels: checked
                          ? [...correct, choice.label]
                          : correct.filter((label) => label !== choice.label),
                      })
                    }
                  />
                  <span aria-hidden="true">Correct</span>
                </Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={disabled || choices.length <= 2}
                  aria-label={`Remove choice ${choice.label}`}
                  title={
                    choices.length <= 2
                      ? 'Multiple choice needs at least two choices'
                      : 'Remove choice'
                  }
                  onClick={() => onChange(removeChoice(edit, index))}
                >
                  <X aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
          {!correct.length && (
            <p className="text-sm text-destructive">
              Mark at least one choice as correct.
            </p>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || choices.length >= choiceLabels.length}
            onClick={() => {
              onChange({
                choices: [
                  ...choices,
                  { label: choiceLabels[choices.length], text: 'New choice' },
                ],
              });
            }}
          >
            <Plus aria-hidden="true" />
            Add choice
          </Button>
        </>
      ) : (
        <>
          {edit.answers.length ? (
            <ul className="space-y-2">
              {edit.answers.map((answer, index) => (
                <li key={index} className="flex items-start gap-2">
                  <Label className="block min-w-0 flex-1 leading-normal">
                    <span className="sr-only">
                      Expected answer
                      {edit.answers.length > 1 ? ` ${index + 1}` : ''}
                    </span>
                    <Textarea
                      value={answer}
                      disabled={disabled}
                      placeholder="Expected answer"
                      className="min-h-8"
                      onChange={(event) =>
                        onChange({
                          answers: edit.answers.map((value, i) =>
                            i === index ? event.target.value : value
                          ),
                        })
                      }
                    />
                  </Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={disabled}
                    aria-label={`Remove expected answer${edit.answers.length > 1 ? ` ${index + 1}` : ''}`}
                    title="Remove answer"
                    onClick={() =>
                      onChange({
                        answers: edit.answers.filter((_, i) => i !== index),
                      })
                    }
                  >
                    <X aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No expected answer. Graders will rely on the rubric.
            </p>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => onChange({ answers: [...edit.answers, ''] })}
          >
            <Plus aria-hidden="true" />
            {edit.answers.length ? 'Add another answer' : 'Add expected answer'}
          </Button>
        </>
      )}
    </section>
  );
}

export function VisualQuestionEditor({
  edit,
  label,
  error,
  parts,
  dragHandle,
  compact,
  deferOffscreen,
  disabled,
  onChange,
  onRemove,
}: {
  edit: QuestionEdit;
  label: string;
  error?: string;
  parts?: ReactNode;
  dragHandle: ReactNode;
  compact: boolean;
  deferOffscreen: boolean;
  disabled: boolean;
  onChange: (edit: QuestionEdit) => void;
  onRemove: () => void;
}) {
  const promptId = useId();
  function update(change: Partial<QuestionEdit>) {
    onChange({ ...edit, ...change });
  }
  return (
    <section
      id={`visual-question-${edit.source_line}`}
      style={!compact && deferOffscreen ? deferredQuestionStyle : undefined}
      className={`scroll-mt-20 ${compact ? compactQuestionRowClassName : 'border bg-background'} ${error ? 'border-destructive' : ''}`}
      aria-label={label}
    >
      <div
        className={
          compact
            ? 'flex items-center gap-2'
            : 'flex items-center gap-2 border-b bg-muted/40 py-1 ps-1 pe-2'
        }
      >
        <QuestionRowHeading
          label={label}
          prompt={compact ? edit.prompt : undefined}
          handle={dragHandle}
        />
        {!compact && (
          <>
            <PointsField edit={edit} disabled={disabled} onChange={update} />
            <DropdownMenu>
              <DropdownMenuTrigger
                disabled={disabled}
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`${label} options`}
                    title={`${label} options`}
                  />
                }
              >
                <Ellipsis aria-hidden="true" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem variant="destructive" onClick={onRemove}>
                  <Trash2 aria-hidden="true" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </div>
      {error && !compact && (
        <p className="flex gap-2 border-b bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
      <div className={compact ? 'ps-4' : 'space-y-6 p-4'}>
        <div hidden={compact} className="space-y-2">
          <Label htmlFor={promptId} className="font-medium">
            Question
          </Label>
          <Textarea
            id={promptId}
            value={edit.prompt}
            disabled={disabled}
            placeholder="What should students answer?"
            onChange={(event) => update({ prompt: event.target.value })}
          />
        </div>
        {parts ? (
          <div className="space-y-3">
            <h4 hidden={compact} className="font-medium">
              Parts
            </h4>
            {parts}
          </div>
        ) : (
          <div hidden={compact} className="space-y-6">
            <AnswerFields edit={edit} disabled={disabled} onChange={update} />
            <RubricEditor
              rubric={edit.rubric}
              points={edit.points}
              disabled={disabled}
              onChange={(rubric) => update({ rubric })}
            />
          </div>
        )}
      </div>
    </section>
  );
}
