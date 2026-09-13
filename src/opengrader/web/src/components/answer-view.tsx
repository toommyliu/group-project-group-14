import { useState } from 'react';
import { Check, ExternalLink } from 'lucide-react';
import type { Answer, Question } from '@/api/queries';
import { GradingText } from '@/components/grading-text';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';

function AnswerCrop({
  url,
  width,
  height,
  submissionId,
  questionId,
}: {
  url: string;
  width: number | null;
  height: number | null;
  submissionId: number;
  questionId: string;
}) {
  const [state, setState] = useState<'loading' | 'loaded' | 'failed'>(
    'loading'
  );
  const aspectRatio = width && height ? `${width} / ${height}` : '4 / 3';
  return (
    <div className="mb-6">
      <div
        className="relative overflow-hidden rounded-md bg-muted outline outline-black/10 dark:outline-white/10"
        style={{ aspectRatio }}
        aria-busy={state === 'loading'}
      >
        {state === 'loading' && (
          <Skeleton className="absolute inset-0 size-full" />
        )}
        {state !== 'failed' && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="absolute inset-0 block"
          >
            <img
              className="size-full object-contain"
              src={url}
              width={width ?? undefined}
              height={height ?? undefined}
              alt={`Scanned answer for submission ${submissionId}, question ${questionId}`}
              onLoad={() => setState('loaded')}
              onError={() => setState('failed')}
            />
          </a>
        )}
      </div>
      <div className="flex min-h-11 items-center" aria-live="polite">
        {state === 'failed' ? (
          <Button
            variant="link"
            className="px-0"
            onClick={() => setState('loading')}
          >
            Image unavailable. Retry
          </Button>
        ) : (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="flex min-h-11 w-fit items-center gap-2 underline underline-offset-4"
          >
            Open full-size image <ExternalLink aria-hidden="true" size={14} />
          </a>
        )}
      </div>
    </div>
  );
}

function Rubric({
  question,
  answer,
}: {
  question: Question;
  answer: Answer | null;
}) {
  const labels = answer?.rubric_labels ?? [];
  const unmatched = labels.filter(
    (label) => !question.rubric.some((option) => option.label === label)
  );
  return (
    <section
      className="min-w-0 border-t pt-8 min-[60rem]:border-0 min-[60rem]:pt-0"
      aria-labelledby="rubric-heading"
    >
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h2
          id="rubric-heading"
          className="text-base leading-snug font-semibold"
        >
          Rubric
        </h2>
        <span className="whitespace-nowrap tabular-nums">
          {answer?.points_awarded != null
            ? `${answer.points_awarded} / ${question.possible_points}`
            : 'Not graded'}
        </span>
      </div>
      {question.rubric.length ? (
        <ul className="grid gap-3">
          {question.rubric.map((option, index) => {
            const selected = labels.includes(option.label);
            return (
              <li
                key={index}
                className={`rounded-lg border p-4 [&>div+div]:mt-3 [&>div+div]:text-sm ${selected ? 'border-foreground bg-muted' : 'border-border'}`}
              >
                <div className="flex items-baseline justify-between gap-4 break-words">
                  <span>{option.label}</span>
                  <span className="whitespace-nowrap tabular-nums">
                    {option.points_delta > 0 ? '+' : ''}
                    {option.points_delta} pts
                  </span>
                </div>
                {option.default_comment && (
                  <GradingText>{option.default_comment}</GradingText>
                )}
                {selected && (
                  <span className="mt-3 flex items-center gap-1.5 text-xs">
                    <Check size={14} aria-hidden="true" /> Selected
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-muted-foreground">
          No rubric options for this question.
        </p>
      )}
      {unmatched.length > 0 && (
        <p>Saved selections absent from this rubric: {unmatched.join(', ')}.</p>
      )}
      <div className="mt-8">
        <h3 className="mb-3 text-sm leading-snug font-semibold">Feedback</h3>
        {answer?.analysis ? (
          <GradingText>{answer.analysis}</GradingText>
        ) : (
          <p className="text-muted-foreground">No feedback recorded.</p>
        )}
      </div>
    </section>
  );
}

export function AnswerView({
  submissionId,
  question,
  answer,
}: {
  submissionId: number;
  question: Question;
  answer: Answer | null;
}) {
  return (
    <div className="grid items-start gap-8 min-[60rem]:grid-cols-[minmax(0,1fr)_minmax(18rem,23rem)] min-[75rem]:gap-16">
      <div className="min-w-0">
        <section aria-labelledby="question-heading">
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
            <h2
              id="question-heading"
              className="text-base leading-snug font-semibold"
            >
              Question {question.id}
            </h2>
            <span className="whitespace-nowrap text-muted-foreground tabular-nums">
              {question.possible_points} points
            </span>
          </div>
          <GradingText>{question.prompt}</GradingText>
          {question.expected_answers.length > 0 && (
            <details className="mt-4 [&>div]:mt-2">
              <summary className="min-h-11 w-fit cursor-pointer py-2.5">
                Expected answer
              </summary>
              {question.expected_answers.map((text, index) => (
                <GradingText key={index}>{text}</GradingText>
              ))}
            </details>
          )}
        </section>
        <section className="mt-8" aria-labelledby="response-heading">
          <h2
            id="response-heading"
            className="mb-4 text-base leading-snug font-semibold"
          >
            Student response
          </h2>
          {answer ? (
            <>
              {answer.response_image_url ? (
                <AnswerCrop
                  key={answer.response_image_url}
                  url={answer.response_image_url}
                  width={answer.response_image_width}
                  height={answer.response_image_height}
                  submissionId={submissionId}
                  questionId={question.id}
                />
              ) : (
                <p className="pt-4 pb-8 text-muted-foreground">
                  No answer image available.
                </p>
              )}
              <h3 className="mb-3 text-sm leading-snug font-semibold">
                Transcription
              </h3>
              {answer.response_text ? (
                <GradingText>{answer.response_text}</GradingText>
              ) : (
                <p className="text-muted-foreground">
                  No response text recorded.
                </p>
              )}
            </>
          ) : (
            <p className="max-w-prose space-y-4 py-8">
              No response recorded for this question.
            </p>
          )}
        </section>
      </div>
      <Rubric question={question} answer={answer} />
    </div>
  );
}
