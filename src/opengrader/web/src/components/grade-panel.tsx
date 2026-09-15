import { Check, RotateCcw } from 'lucide-react';
import type { Answer, Question } from '@/api/queries';
import { useGradeEditor } from '@/hooks/use-grade-editor';
import { GradingText } from './grading-text';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Kbd } from './ui/kbd';
import { Textarea } from './ui/textarea';
import { Input } from './ui/input';
import { GradeRecoveryDialog } from './grade-recovery-dialog';

export function GradePanel({
  assignmentId,
  question,
  answer,
  navigating,
  onNext,
}: {
  assignmentId: string;
  question: Question;
  answer: Answer;
  navigating: boolean;
  onNext: (() => void) | undefined;
}) {
  const {
    saved,
    draft,
    saving,
    feedback,
    scoreInput,
    message,
    error,
    dirty,
    validScore,
    score,
    busy,
    change,
    choose,
    reload,
    requestReload,
    reloadOpen,
    setReloadOpen,
    blocker,
    discardAndLeave,
    retryAndLeave,
  } = useGradeEditor({ assignmentId, question, answer, navigating, onNext });
  return (
    <section
      className="min-w-0 rounded-xl border bg-background p-4 sm:p-5"
      aria-labelledby="rubric-heading"
      aria-busy={saving}
    >
      <div className="mb-5 grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 id="rubric-heading" className="font-semibold">
            Rubric
          </h2>
          <p
            role="status"
            className="min-w-0 text-sm wrap-anywhere text-muted-foreground"
          >
            {error
              ? 'Not saved'
              : saving
                ? 'Saving…'
                : dirty
                  ? 'Saving…'
                  : message ||
                    (saved.points_awarded === null ? 'Not graded' : 'Saved')}
          </p>
        </div>
        <p className="text-end text-xl font-semibold whitespace-nowrap tabular-nums">
          {validScore ? (score ?? '—') : '—'}{' '}
          <span className="text-sm font-normal text-muted-foreground">
            / {question.possible_points}
          </span>
        </p>
      </div>
      <fieldset disabled={busy} className="grid gap-2">
        <legend className="sr-only">Rubric choices</legend>
        {question.rubric.map((option, index) => {
          const selected = !draft.manual && draft.labels.includes(option.label);
          return (
            <div
              key={index}
              className={`rounded-lg border ${selected ? 'border-foreground bg-muted' : 'border-border'}`}
            >
              <Button
                variant="ghost"
                type="button"
                aria-pressed={selected}
                onClick={() => choose(index)}
                className="block h-auto w-full rounded-lg p-3 text-start whitespace-normal"
              >
                <span className="flex items-start gap-3">
                  <span
                    className="flex size-6 shrink-0 items-center justify-center rounded border text-xs"
                    aria-hidden="true"
                  >
                    {selected ? (
                      <Check size={14} />
                    ) : index < 9 ? (
                      index + 1
                    ) : (
                      '·'
                    )}
                  </span>
                  <span className="min-w-0 flex-1 break-words">
                    {option.label}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {option.points_delta > 0 ? '+' : ''}
                    {option.points_delta}
                  </span>
                </span>
              </Button>
              {option.default_comment && (
                <div className="px-3 pb-3 text-xs text-muted-foreground">
                  <GradingText>{option.default_comment}</GradingText>
                </div>
              )}
            </div>
          );
        })}
        {!question.rubric.length && (
          <p className="text-muted-foreground">
            No rubric items. Enter a score below.
          </p>
        )}
      </fieldset>
      {draft.labels.some(
        (label) => !question.rubric.some((option) => option.label === label)
      ) && (
        <p className="mt-3 text-sm" role="alert">
          Some saved rubric items are no longer available. Choose a current item
          before saving.
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() =>
            change({ ...draft, labels: [], manual: false, score: '' })
          }
        >
          Clear grade
        </Button>
        <Button
          variant="ghost"
          disabled={busy}
          aria-pressed={draft.manual}
          onClick={() =>
            change({
              ...draft,
              labels: [],
              manual: !draft.manual,
              score: score?.toString() ?? '',
            })
          }
        >
          Enter score
        </Button>
      </div>
      {draft.manual && (
        <div className="mt-3 grid gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="manual-score" className="text-base leading-normal">
              Points awarded
            </Label>
            {error && (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Reload saved answer"
                title="Reload saved answer"
                disabled={busy || saving}
                onClick={requestReload}
              >
                <RotateCcw aria-hidden="true" />
              </Button>
            )}
          </div>
          <Input
            ref={scoreInput}
            className="min-h-11 text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground md:text-sm"
            id="manual-score"
            type="number"
            min="0"
            step="any"
            value={draft.score}
            disabled={busy}
            aria-invalid={!validScore}
            aria-describedby="score-hint"
            onChange={(event) =>
              change({ ...draft, score: event.target.value })
            }
          />
          <p
            id="score-hint"
            role={error ? 'alert' : undefined}
            className="text-xs text-muted-foreground"
          >
            {error ||
              `Leave blank for ungraded. Extra credit may exceed ${question.possible_points} points.`}
          </p>
        </div>
      )}
      <div className="mt-6 grid gap-2">
        <Label
          htmlFor="grading-feedback"
          className="text-base leading-normal font-medium"
        >
          Feedback <Kbd>C</Kbd>
        </Label>
        <Textarea
          ref={feedback}
          className="field-sizing-fixed resize-y text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground md:text-sm"
          id="grading-feedback"
          rows={4}
          value={draft.analysis}
          maxLength={50000}
          disabled={busy}
          placeholder="Explain the score or how to improve."
          onChange={(event) =>
            change({ ...draft, analysis: event.target.value })
          }
        />
      </div>
      {error && !draft.manual && (
        <p role="alert" className="mt-2 text-sm">
          {error}
        </p>
      )}
      <GradeRecoveryDialog
        mode={
          reloadOpen ? 'reload' : blocker.status === 'blocked' ? 'leave' : null
        }
        busy={busy || saving}
        onCancel={() => {
          setReloadOpen(false);
          blocker.reset?.();
        }}
        onDiscard={reloadOpen ? () => void reload() : discardAndLeave}
        onRetry={() => void retryAndLeave()}
      />
    </section>
  );
}
