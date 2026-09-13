import { useRouterState } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react';
import { validateGradingSearch } from '@/routes/assignment-data';
import { assignmentRoute } from '@/router';
import { AnswerView } from './answer-view';
import { PageHeading } from './page-heading';
import { Button } from './ui/button';

export default function AssignmentView() {
  const { assignment, submissions, submission, questions, question, answer } =
    assignmentRoute.useLoaderData();
  const navigate = assignmentRoute.useNavigate();
  const pendingSelection = useRouterState({
    select: (state) =>
      state.isLoading &&
      state.location.pathname === state.resolvedLocation?.pathname
        ? validateGradingSearch(state.location.search)
        : undefined,
  });
  const loadingSubmission =
    pendingSelection !== undefined &&
    (pendingSelection.submission ?? submissions[0]?.id) !== submission?.id;
  const loadingQuestion =
    pendingSelection !== undefined &&
    !loadingSubmission &&
    (pendingSelection.question ?? questions[0]?.id) !== question?.id;
  const index = submission ? submissions.indexOf(submission) : -1;

  function selectSubmission(id: number) {
    void navigate({
      search: { submission: id, question: question?.id },
      resetScroll: false,
    });
  }

  return (
    <>
      <PageHeading
        title={assignment.title}
        description="Review student responses and existing grades."
      />
      {submission ? (
        <>
          <div className="flex flex-wrap items-end gap-6 border-b pb-6">
            <div className="grid min-w-0 gap-2">
              <label htmlFor="submission" className="flex items-center gap-2">
                Submission
                <span className="size-4" role="status">
                  {loadingSubmission && (
                    <>
                      <LoaderCircle
                        className="size-4 motion-safe:animate-spin"
                        aria-hidden="true"
                      />
                      <span className="sr-only">Loading submission</span>
                    </>
                  )}
                </span>
              </label>
              <select
                className="min-h-11 w-52 max-w-full rounded-md border border-input bg-background px-3 py-2 text-base text-foreground"
                id="submission"
                value={submission.id}
                onChange={(event) =>
                  selectSubmission(Number(event.target.value))
                }
              >
                {submissions.map((item) => (
                  <option key={item.id} value={item.id}>
                    Submission {item.id}
                    {item.version ? ` · Version ${item.version}` : ''}
                  </option>
                ))}
              </select>
            </div>
            {question && (
              <div className="grid min-w-0 gap-2">
                <label htmlFor="question" className="flex items-center gap-2">
                  Question
                  <span className="size-4" role="status">
                    {loadingQuestion && (
                      <>
                        <LoaderCircle
                          className="size-4 motion-safe:animate-spin"
                          aria-hidden="true"
                        />
                        <span className="sr-only">Loading question</span>
                      </>
                    )}
                  </span>
                </label>
                <select
                  className="min-h-11 w-44 max-w-full rounded-md border border-input bg-background px-3 py-2 text-base text-foreground"
                  id="question"
                  value={question.id}
                  onChange={(event) =>
                    void navigate({
                      search: {
                        submission: submission.id,
                        question: event.target.value,
                      },
                      resetScroll: false,
                    })
                  }
                >
                  {questions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.possible_points} points
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="ms-auto flex min-h-11 flex-wrap items-center gap-3">
              <span className="text-sm whitespace-nowrap text-muted-foreground tabular-nums">
                {index + 1} of {submissions.length}
              </span>
              <Button
                variant="outline"
                disabled={index === 0}
                onClick={() => selectSubmission(submissions[index - 1].id)}
              >
                <ChevronLeft aria-hidden="true" /> Previous
              </Button>
              <Button
                variant="outline"
                disabled={index === submissions.length - 1}
                onClick={() => selectSubmission(submissions[index + 1].id)}
              >
                Next <ChevronRight aria-hidden="true" />
              </Button>
            </div>
          </div>
          <div className="min-h-[32rem] pt-6">
            {question ? (
              <AnswerView
                key={`${submission.id}/${question.id}`}
                submissionId={submission.id}
                question={question}
                answer={answer}
              />
            ) : (
              <p className="py-8 text-muted-foreground">
                No questions available for this submission.
              </p>
            )}
          </div>
        </>
      ) : (
        <p className="min-h-80 py-8 text-muted-foreground">
          No submissions available for this assignment.
        </p>
      )}
    </>
  );
}
