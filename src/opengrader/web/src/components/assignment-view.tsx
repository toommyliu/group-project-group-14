import { usePreference } from '@/hooks/use-preference';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useGradingHotkeys as useHotkeys } from '@/hooks/use-grading-hotkeys';
import { submissionsQuery, answerQuery } from '@/api/queries';
import { nextUngraded } from '@/lib/grading';
import { GradingShortcuts } from './grading-shortcuts';
import { useRouterState } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react';
import { validateGradingSearch } from '@/routes/assignment-data';
import { assignmentRoute } from '@/router';
import { AnswerView } from './answer-view';
import { PageHeading } from './page-heading';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Kbd } from './ui/kbd';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from './ui/select';

export default function AssignmentView({
  data,
}: {
  data: typeof assignmentRoute.types.loaderData;
}) {
  const { assignment, submission, questions, question } = data;
  const { data: submissions } = useQuery({
    ...submissionsQuery(assignment.id),
    initialData: data.submissions,
  });
  const { data: answer } = useQuery({
    ...answerQuery(assignment.id, submission?.id ?? 0, question?.id ?? ''),
    enabled: Boolean(data.answer),
    initialData: data.answer ?? undefined,
    refetchOnWindowFocus: false,
  });
  const navigating = useRouterState({ select: (state) => state.isLoading });
  const [progressFormat, setProgressFormat] = useState<'count' | 'percentage'>(
    'count'
  );
  const [shortcutsEnabled] = usePreference('gradingShortcuts');
  const navigate = assignmentRoute.useNavigate();
  const pendingSelection = useRouterState({
    select: (state) =>
      state.isLoading &&
      state.location.pathname === '/submissions' &&
      state.location.search.assignment === assignment.id &&
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
  const index = submissions.findIndex((item) => item.id === submission?.id);
  const previous = index > 0 ? submissions[index - 1] : undefined;
  const next = index >= 0 ? submissions[index + 1] : undefined;

  const questionIndex = questions.findIndex((item) => item.id === question?.id);
  const ungraded =
    submission && question
      ? nextUngraded(submissions, submission.id, question.id)
      : undefined;
  const applicable = question
    ? submissions.filter((item) => question.id in item.question_scores)
    : [];
  const graded = question
    ? applicable.filter((item) => item.question_scores[question.id] !== null)
        .length
    : 0;
  const gradedPercentage = applicable.length
    ? Math.round((graded / applicable.length) * 100)
    : 0;
  const progressValue =
    progressFormat === 'count'
      ? `${graded} of ${applicable.length}`
      : `${gradedPercentage}%`;

  function selectQuestion(id: string) {
    void navigate({
      search: {
        assignment: assignment.id,
        submission: submission?.id,
        question: id,
      },
      resetScroll: false,
    });
  }

  useHotkeys(
    [
      { hotkey: 'J', callback: () => next && selectSubmission(next.id) },
      {
        hotkey: 'K',
        callback: () => previous && selectSubmission(previous.id),
      },
      {
        hotkey: 'N',
        callback: () => ungraded && selectSubmission(ungraded.id),
      },
      {
        hotkey: '[',
        callback: () =>
          questions[questionIndex - 1] &&
          selectQuestion(questions[questionIndex - 1]!.id),
      },
      {
        hotkey: ']',
        callback: () =>
          questions[questionIndex + 1] &&
          selectQuestion(questions[questionIndex + 1]!.id),
      },
    ],
    {
      enabled: shortcutsEnabled && Boolean(submission) && !navigating,
      ignoreInputs: true,
      requireReset: true,
    }
  );

  function selectSubmission(id: number) {
    void navigate({
      search: {
        assignment: assignment.id,
        submission: id,
        question: question?.id,
      },
      resetScroll: false,
    });
  }

  return (
    <>
      <PageHeading
        title="Submissions"
        description={assignment.title}
        actions={<GradingShortcuts />}
      />
      {submission ? (
        <div className="@container">
          <div className="grid items-end gap-4 border-b pb-6 @min-[54rem]:grid-cols-[minmax(0,1fr)_auto]">
            <div className="grid min-w-0 gap-4 @min-[28rem]:grid-cols-[minmax(0,13rem)_minmax(0,11rem)]">
              <div className="grid min-w-0 gap-2">
                <Label
                  htmlFor="submission"
                  className="text-base leading-normal"
                >
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
                </Label>
                <Select
                  value={submission.id}
                  disabled={navigating}
                  onValueChange={(id) => {
                    if (id !== null) selectSubmission(id);
                  }}
                >
                  <SelectTrigger
                    id="submission"
                    className="min-h-11 w-full text-sm"
                  >
                    <SelectValue>{`Submission ${submission.id}${submission.version ? ` · Version ${submission.version}` : ''}`}</SelectValue>
                  </SelectTrigger>
                  <SelectContent align="start">
                    {submissions.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        Submission {item.id}
                        {item.version ? ` · Version ${item.version}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {question && (
                <div className="grid min-w-0 gap-2">
                  <Label
                    htmlFor="question"
                    className="text-base leading-normal"
                  >
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
                  </Label>
                  <Select
                    value={question.id}
                    disabled={navigating}
                    onValueChange={(id) => {
                      if (id !== null) selectQuestion(id);
                    }}
                  >
                    <SelectTrigger
                      id="question"
                      className="min-h-11 w-full text-sm"
                    >
                      <SelectValue>{`${question.id} · ${question.possible_points} points`}</SelectValue>
                    </SelectTrigger>
                    <SelectContent align="start">
                      {questions.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.id} · {item.possible_points} points
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <div className="flex min-h-11 flex-wrap items-center gap-3">
              <span className="basis-full text-sm whitespace-nowrap text-muted-foreground tabular-nums @min-[28rem]:basis-auto">
                {index + 1} of {submissions.length}
              </span>
              <Button
                variant="outline"
                disabled={!previous || navigating}
                onClick={() => previous && selectSubmission(previous.id)}
              >
                <ChevronLeft aria-hidden="true" /> Previous
              </Button>
              <Button
                variant="outline"
                disabled={!next || navigating}
                onClick={() => next && selectSubmission(next.id)}
              >
                Next <ChevronRight aria-hidden="true" />
              </Button>
              <Button
                disabled={!ungraded || navigating}
                title="Next ungraded response (N)"
                onClick={() => ungraded && selectSubmission(ungraded.id)}
              >
                Next ungraded
              </Button>
            </div>
          </div>
          {question && (
            <div className="flex flex-col gap-4 pt-4 text-sm text-muted-foreground @min-[54rem]:flex-row @min-[54rem]:items-center">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                <progress
                  className="h-2 w-full accent-foreground @min-[28rem]:w-32"
                  aria-label={`Grading progress for question ${question.id}`}
                  max={applicable.length || 1}
                  value={graded}
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  aria-pressed={progressFormat === 'percentage'}
                  aria-label={
                    progressFormat === 'count'
                      ? `${progressValue} responses graded. Show percentage.`
                      : `${progressValue} of responses graded. Show count.`
                  }
                  title={
                    progressFormat === 'count'
                      ? 'Show percentage'
                      : 'Show response count'
                  }
                  className="relative text-sm tabular-nums after:absolute after:-inset-y-2"
                  onClick={() =>
                    setProgressFormat((current) =>
                      current === 'count' ? 'percentage' : 'count'
                    )
                  }
                >
                  {progressValue}
                </Button>
                <span className="min-w-0 flex-1" title={question.id}>
                  responses graded for{' '}
                  <span className="break-all">{question.id}</span>
                </span>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2 @min-[54rem]:ms-auto">
                <Button
                  variant="ghost"
                  disabled={questionIndex <= 0 || navigating}
                  onClick={() =>
                    selectQuestion(questions[questionIndex - 1]!.id)
                  }
                >
                  Previous question <Kbd>[</Kbd>
                </Button>
                <Button
                  variant="ghost"
                  disabled={questionIndex >= questions.length - 1 || navigating}
                  onClick={() =>
                    selectQuestion(questions[questionIndex + 1]!.id)
                  }
                >
                  Next question <Kbd>]</Kbd>
                </Button>
              </div>
            </div>
          )}
          <div className="min-h-[32rem] pt-6">
            {question ? (
              <AnswerView
                key={`${submission.id}/${question.id}`}
                assignmentId={assignment.id}
                navigating={navigating}
                onNext={
                  ungraded ? () => selectSubmission(ungraded.id) : undefined
                }
                submissionId={submission.id}
                question={question}
                answer={data.answer ? (answer ?? data.answer) : null}
              />
            ) : (
              <p className="py-8 text-muted-foreground">
                No questions available for this submission.
              </p>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-8">
          <p className="border-t pt-6 text-muted-foreground">
            No submissions yet.
          </p>
        </div>
      )}
    </>
  );
}
