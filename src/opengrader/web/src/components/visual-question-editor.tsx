import { useRef, useState, type ReactNode } from 'react';
import { rescaleRubric } from '@/lib/rubric-points';
import type { QuestionEdit } from '@/api/queries';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Textarea } from './ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { RubricEditor } from './rubric-editor';
import {
  compactQuestionRowClassName,
  QuestionRowHeading,
} from './question-row-heading';

export function VisualQuestionEditor({
  edit,
  label,
  hasSubquestions,
  dragHandle,
  compact,
  disabled,
  onChange,
  onRemove,
}: {
  edit: QuestionEdit;
  label: string;
  hasSubquestions: boolean;
  dragHandle: ReactNode;
  compact: boolean;
  disabled: boolean;
  onChange: (edit: QuestionEdit) => void;
  onRemove: () => void;
}) {
  const [pointsInput, setPointsInput] = useState(String(edit.points));
  const pointsBaseline = useRef({ points: edit.points, rubric: edit.rubric });
  const choices = edit.choices ?? [];
  const correct = edit.correct_choice_labels ?? [];
  function update(change: Partial<QuestionEdit>) {
    onChange({ ...edit, ...change });
  }
  function updateRubric(rubric: QuestionEdit['rubric']) {
    const previousCorrect = edit.rubric.find(
      (item) => item.label === 'Correct'
    )?.points_delta;
    const previousIncorrect = edit.rubric.find(
      (item) => item.label === 'Incorrect'
    )?.points_delta;
    const correct = rubric.find(
      (item) => item.label === 'Correct'
    )?.points_delta;
    const incorrect = rubric.find(
      (item) => item.label === 'Incorrect'
    )?.points_delta;
    const changedAward =
      correct != null &&
      correct !== previousCorrect &&
      incorrect === previousIncorrect &&
      previousIncorrect === 0;
    const changedDeduction =
      incorrect != null &&
      incorrect !== previousIncorrect &&
      correct === previousCorrect &&
      previousCorrect === 0;
    const points = changedAward
      ? correct
      : changedDeduction
        ? -incorrect!
        : null;
    if (
      points != null &&
      Number.isSafeInteger(points) &&
      points >= 0 &&
      points !== edit.points
    ) {
      setPointsInput(String(points));
      update({
        points,
        rubric: rescaleRubric(rubric, edit.points, points, changedAward),
      });
    } else update({ rubric });
  }
  return (
    <section
      id={`visual-question-${edit.source_line}`}
      className={
        compact
          ? `scroll-mt-20 ${compactQuestionRowClassName}`
          : 'scroll-mt-20 space-y-5 border-t pt-6'
      }
      aria-label={label}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <QuestionRowHeading
          label={label}
          prompt={compact ? edit.prompt : undefined}
          handle={dragHandle}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={onRemove}
          hidden={compact}
        >
          Remove question
        </Button>
      </div>
      <div hidden={compact} className="space-y-5">
        <Label className="block space-y-2 text-sm leading-normal">
          <span>Text</span>
          <Textarea
            value={edit.prompt}
            disabled={disabled}
            onChange={(event) => update({ prompt: event.target.value })}
          />
        </Label>
        <div className="flex flex-wrap items-end gap-4">
          <Label className="flex w-28 flex-col items-stretch gap-1 text-sm leading-normal">
            <span>Points</span>
            <Input
              type="number"
              min="0"
              step="1"
              required
              value={pointsInput}
              disabled={disabled}
              onFocus={() => {
                pointsBaseline.current = {
                  points: edit.points,
                  rubric: edit.rubric,
                };
              }}
              onChange={(event) => {
                const value = event.target.value;
                setPointsInput(value);
                const points = Number(value);
                if (
                  value.trim() &&
                  Number.isSafeInteger(points) &&
                  points >= 0 &&
                  points !== edit.points
                ) {
                  update({
                    points,
                    rubric: rescaleRubric(
                      pointsBaseline.current.rubric,
                      pointsBaseline.current.points,
                      points
                    ),
                  });
                }
              }}
              onBlur={() => setPointsInput(String(edit.points))}
            />
          </Label>
          {!hasSubquestions && (
            <Label className="flex min-w-40 flex-1 flex-col items-stretch gap-1 text-sm leading-normal">
              <span>Type</span>
              <Select
                items={[
                  // TODO: FREE RESPONSE?
                  { value: 'written', label: 'Written answer' },
                  { value: 'choices', label: 'Multiple choice' },
                ]}
                value={choices.length ? 'choices' : 'written'}
                disabled={disabled}
                onValueChange={(value) =>
                  update(
                    value === 'choices'
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
                  )
                }
              >
                <SelectTrigger aria-label="Type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="written">Written answer</SelectItem>
                  <SelectItem value="choices">Multiple choice</SelectItem>
                </SelectContent>
              </Select>
            </Label>
          )}
        </div>
        {!!edit.rubric.length && (
          <p className="text-sm text-muted-foreground">
            Changing points scales rubric values proportionally. Bonus points
            stay fixed.
          </p>
        )}
        {!hasSubquestions &&
          (choices.length ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Select each correct choice.
              </p>
              {choices.map((choice, index) => (
                <div
                  key={choice.label}
                  className="flex flex-wrap items-center gap-2"
                >
                  <Label className="flex min-h-10 items-center gap-2 text-sm leading-normal">
                    <Checkbox
                      checked={correct.includes(choice.label)}
                      disabled={disabled}
                      aria-label={`Choice ${choice.label} is correct`}
                      onCheckedChange={(checked) =>
                        update({
                          correct_choice_labels: checked
                            ? [...correct, choice.label]
                            : correct.filter((label) => label !== choice.label),
                        })
                      }
                    />
                    {choice.label}
                  </Label>
                  <Label className="block min-w-0 flex-[1_1_10rem] leading-normal">
                    <span className="sr-only">Choice {choice.label}</span>
                    <Input
                      value={choice.text}
                      disabled={disabled}
                      onChange={(event) =>
                        update({
                          choices: choices.map((value, i) =>
                            i === index
                              ? { ...value, text: event.target.value }
                              : value
                          ),
                        })
                      }
                    />
                  </Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={disabled || choices.length <= 2}
                    aria-label={`Remove choice ${choice.label}`}
                    onClick={() =>
                      update({
                        choices: choices.filter(
                          (value) => value.label !== choice.label
                        ),
                        correct_choice_labels: correct.filter(
                          (label) => label !== choice.label
                        ),
                      })
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled || choices.length >= 26}
                onClick={() => {
                  const label = Array.from('ABCDEFGHIJKLMNOPQRSTUVWXYZ').find(
                    (value) => !choices.some((choice) => choice.label === value)
                  );
                  if (label)
                    update({
                      choices: [...choices, { label, text: 'New choice' }],
                    });
                }}
              >
                Add choice
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              {edit.answers.map((answer, index) => (
                <div key={index} className="space-y-2">
                  <Label className="block space-y-2 text-sm leading-normal">
                    <span>
                      Expected answer {edit.answers.length > 1 ? index + 1 : ''}
                    </span>
                    <Textarea
                      value={answer}
                      disabled={disabled}
                      onChange={(event) =>
                        update({
                          answers: edit.answers.map((value, i) =>
                            i === index ? event.target.value : value
                          ),
                        })
                      }
                    />
                  </Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={disabled}
                    onClick={() =>
                      update({
                        answers: edit.answers.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove answer
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() => update({ answers: [...edit.answers, ''] })}
              >
                Add expected answer
              </Button>
            </div>
          ))}
        {!hasSubquestions && (
          <RubricEditor
            rubric={edit.rubric}
            points={edit.points}
            disabled={disabled}
            onChange={updateRubric}
          />
        )}
      </div>
    </section>
  );
}
