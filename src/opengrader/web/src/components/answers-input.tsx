import { useId } from 'react';
import type { AnswerField } from '@/api/queries';
import type { AnswerDraft } from '@/lib/answers';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

/**
 * One question's answers: a tick-box per option for a multiple-choice
 * question (one, or every correct one), else a box taking one accepted
 * answer per line. Left empty, the question is graded by hand.
 */
export function AnswersInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: AnswerField;
  value: AnswerDraft;
  disabled?: boolean;
  onChange: (value: AnswerDraft) => void;
}) {
  const id = useId();
  if (field.choices.length)
    return (
      <div className="grid gap-1.5" role="group" aria-label="Correct option">
        {field.choices.map((choice) => (
          <Label
            key={choice.label}
            className="flex items-start gap-2 text-sm leading-normal font-normal"
          >
            <Checkbox
              className="mt-0.5"
              checked={value.includes(choice.label)}
              disabled={disabled}
              onCheckedChange={(checked) =>
                onChange(
                  checked
                    ? [...value, choice.label].sort()
                    : value.filter((label) => label !== choice.label)
                )
              }
            />
            <span className="wrap-anywhere">
              {choice.label}) {choice.text}
            </span>
          </Label>
        ))}
      </div>
    );
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="sr-only">
        Accepted answers, one per line
      </Label>
      <Textarea
        id={id}
        rows={Math.max(2, value.length)}
        value={value.join('\n')}
        disabled={disabled}
        placeholder="One accepted answer per line"
        onChange={(event) => onChange(event.target.value.split('\n'))}
      />
    </div>
  );
}
