import type { Answer, Question } from '@/api/queries';
import { GradingText } from './grading-text';
import { AnswerCrop } from './answer-crop';
import { GradePanel } from './grade-panel';

export function AnswerView({
  assignmentId,
  navigating,
  onNext,
  submissionId,
  question,
  answer,
}: {
  assignmentId: string;
  navigating: boolean;
  onNext: (() => void) | undefined;
  submissionId: number;
  question: Question;
  answer: Answer | null;
}) {
  return (
    <div className="grid items-start gap-8 min-[60rem]:grid-cols-[minmax(0,1fr)_minmax(18rem,23rem)] min-[75rem]:gap-8">
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
              <h3 className="mt-6 mb-3 text-sm leading-snug font-semibold">
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
      {answer ? (
        <GradePanel
          assignmentId={assignmentId}
          question={question}
          answer={answer}
          navigating={navigating}
          onNext={onNext}
        />
      ) : (
        <p className="rounded-lg border p-5 text-muted-foreground">
          This question has no extracted response to grade. Check the submission
          scan.
        </p>
      )}
    </div>
  );
}
