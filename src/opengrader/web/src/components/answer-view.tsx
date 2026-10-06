import { workspaceUrl } from '@/api/client';
import type { ReactNode } from 'react';
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
  header,
}: {
  assignmentId: string;
  navigating: boolean;
  onNext: (() => void) | undefined;
  submissionId: number;
  question: Question;
  answer: Answer | null;
  header: ReactNode;
}) {
  return (
    <div className="grid gap-8 border-t pt-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,26rem)] lg:grid-rows-[minmax(0,1fr)] lg:gap-0">
      <div className="flex min-w-0 flex-col gap-4 lg:-ms-2 lg:me-3 lg:-mt-2 lg:[scrollbar-gutter:stable] lg:overflow-y-auto lg:overscroll-contain lg:ps-2 lg:pe-3 lg:pt-2 lg:pb-4">
        <section aria-labelledby="question-heading">
          <h2
            id="question-heading"
            className="flex min-h-11 items-center text-base leading-snug font-semibold"
          >
            Question
          </h2>
          <GradingText>{question.prompt}</GradingText>
        </section>
        {question.expected_answers.length > 0 && (
          <section aria-labelledby="expected-heading">
            <h2
              id="expected-heading"
              className="flex min-h-11 items-center text-base leading-snug font-semibold"
            >
              Expected answer
            </h2>
            <div className="grid gap-3">
              {question.expected_answers.map((text, index) => (
                <GradingText key={index}>{text}</GradingText>
              ))}
            </div>
          </section>
        )}
        <section aria-labelledby="response-heading">
          <h2
            id="response-heading"
            className="flex min-h-11 items-center text-base leading-snug font-semibold"
          >
            Student response
          </h2>
          {answer ? (
            <>
              {answer.response_text && (
                <div className="mb-4">
                  <h3 className="mb-1 text-sm leading-snug text-muted-foreground">
                    Transcription
                  </h3>
                  <GradingText>{answer.response_text}</GradingText>
                </div>
              )}
              {answer.response_image_url ? (
                <AnswerCrop
                  key={answer.response_image_url}
                  url={workspaceUrl(answer.response_image_url)}
                  width={answer.response_image_width}
                  height={answer.response_image_height}
                  submissionId={submissionId}
                  questionId={question.id}
                />
              ) : (
                <p className="max-w-prose pt-4 pb-8 text-muted-foreground">
                  Nothing to show for this question. The outline had no box for
                  it when this submission was created.
                </p>
              )}
            </>
          ) : (
            <p className="max-w-prose py-8">
              No response recorded for this question.
            </p>
          )}
        </section>
      </div>
      <aside
        aria-label="Grading"
        className="flex min-w-0 flex-col border-t pt-6 lg:min-h-0 lg:border-s lg:border-t-0 lg:ps-4 lg:pt-0"
      >
        <div className="flex flex-col lg:-me-4 lg:-mt-2 lg:min-h-0 lg:flex-1 lg:[scrollbar-gutter:stable] lg:overflow-y-auto lg:overscroll-contain lg:ps-2 lg:pe-4 lg:pt-2 lg:pb-4">
          {header && (
            <div className="mb-6 shrink-0 lg:sticky lg:top-0 lg:z-10 lg:mb-0 lg:bg-background lg:pb-6">
              {header}
            </div>
          )}
          {answer ? (
            <GradePanel
              assignmentId={assignmentId}
              question={question}
              answer={answer}
              navigating={navigating}
              onNext={onNext}
            />
          ) : (
            <p className="text-muted-foreground">
              This question has no extracted response to grade. Check the
              submission scan.
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
