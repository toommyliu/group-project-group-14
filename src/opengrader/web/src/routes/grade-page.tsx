import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useRouterState } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, CircleCheck, PenLine } from 'lucide-react';
import {
  useGradingShortcutHints,
  useGradingHotkeys,
} from '@/hooks/use-grading-hotkeys';
import {
  aiDraftJobQuery,
  submissionsQuery,
  answerQuery,
  type Submission,
} from '@/api/queries';
import { nextUngraded } from '@/lib/grading';
import { submissionIdentity } from '@/lib/submission-identity';
import { usePreferences } from '@/stores/preferences';
import { validateGradingSearch } from '@/routes/assignment-data';
import { gradeRoute } from '@/router';
import { AnswerView } from '@/components/answer-view';
import { AiDrafts, QuestionAiStatus } from '@/components/ai-drafts';
import { questionAiLabel } from '@/lib/ai-draft';
import { GradingShortcuts } from '@/components/grading-shortcuts';
import { AnonymousGrading } from '@/components/anonymous-grading';
import { LoadingIndicator } from '@/components/loading';
import { PageHeading } from '@/components/page-heading';
import { Button } from '@/components/ui/button';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { Label } from '@/components/ui/label';
import { Kbd } from '@/components/ui/kbd';
import type { GradingAction } from '@/lib/grading-shortcuts';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';

function SubmissionLabel({
  submission,
  anonymous,
}: {
  submission: Submission;
  anonymous: boolean;
}) {
  const { name, detail } = submissionIdentity(submission, anonymous);
  return (
    <>
      <span className="truncate">{name}</span>
      {detail && (
        <span className="shrink-0 text-muted-foreground">{detail}</span>
      )}
    </>
  );
}

export default function GradePage({
  data,
}: {
  data: typeof gradeRoute.types.loaderData;
}) {
  const { assignment, submission, questions, question } = data;
  const anonymous = usePreferences((state) => state.anonymousGrading);
  const identity = submission && submissionIdentity(submission, anonymous);
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
  const { data: aiJob } = useQuery(aiDraftJobQuery(assignment.id));
  const navigating = useRouterState({ select: (state) => state.isLoading });
  const { shortcut, shortcutAria } = useGradingShortcutHints();
  const hint = (label: string, action: GradingAction) =>
    shortcut(action) ? `${label} (${shortcut(action)})` : label;
  const navigate = gradeRoute.useNavigate();
  const pendingSelection = useRouterState({
    select: (state) => {
      const path = `/assignments/${assignment.id}/grade`;
      return state.isLoading &&
        state.location.pathname === path &&
        state.resolvedLocation?.pathname === path
        ? validateGradingSearch(state.location.search)
        : undefined;
    },
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
  const previousQuestion =
    questionIndex > 0 ? questions[questionIndex - 1] : undefined;
  const nextQuestion =
    questionIndex >= 0 ? questions[questionIndex + 1] : undefined;
  const ungraded =
    submission && question
      ? nextUngraded(submissions, submission.id, question.id)
      : undefined;
  const progress = useMemo(
    () =>
      new Map(
        questions.map(({ id }) => {
          const applicable = submissions.filter(
            (item) => id in item.question_scores
          );
          const graded = applicable.filter(
            (item) => item.question_scores[id] !== null
          ).length;
          return [id, { graded, total: applicable.length }];
        })
      ),
    [questions, submissions]
  );
  const { graded, total } = (question && progress.get(question.id)) ?? {
    graded: 0,
    total: 0,
  };
  const gradedPercentage = total ? Math.round((graded / total) * 100) : 0;

  function selectQuestion(id: string) {
    void navigate({
      to: '/assignments/$assignmentId/grade',
      params: { assignmentId: assignment.id },
      search: { submission: submission?.id, question: id },
      resetScroll: false,
    });
  }

  function selectSubmission(id: number) {
    void navigate({
      to: '/assignments/$assignmentId/grade',
      params: { assignmentId: assignment.id },
      search: { submission: id, question: question?.id },
      resetScroll: false,
    });
  }

  useGradingHotkeys(
    [
      {
        action: 'nextSubmission',
        callback: () => next && selectSubmission(next.id),
      },
      {
        action: 'previousSubmission',
        callback: () => previous && selectSubmission(previous.id),
      },
      {
        action: 'nextUngraded',
        callback: () => ungraded && selectSubmission(ungraded.id),
      },
      {
        action: 'previousQuestion',
        callback: () => previousQuestion && selectQuestion(previousQuestion.id),
      },
      {
        action: 'nextQuestion',
        callback: () => nextQuestion && selectQuestion(nextQuestion.id),
      },
    ],
    {
      enabled: Boolean(submission) && !navigating,
      ignoreInputs: true,
      requireReset: true,
    }
  );

  const primary = ungraded ? (
    <Button
      className="max-sm:flex-1"
      disabled={navigating}
      onClick={() => selectSubmission(ungraded.id)}
      aria-keyshortcuts={shortcutAria('nextUngraded')}
    >
      Next ungraded{' '}
      {shortcut('nextUngraded') && <Kbd>{shortcut('nextUngraded')}</Kbd>}
    </Button>
  ) : graded === total && nextQuestion ? (
    <Button
      className="max-sm:flex-1"
      disabled={navigating}
      onClick={() => selectQuestion(nextQuestion.id)}
    >
      Next question{' '}
      {shortcut('nextQuestion') && <Kbd>{shortcut('nextQuestion')}</Kbd>}
    </Button>
  ) : (
    <Button
      className="max-sm:flex-1"
      disabled
      title="No other ungraded responses to this question"
    >
      Next ungraded
    </Button>
  );
  const questionHeader = question && (
    <div className="grid gap-2">
      <div className="flex min-w-0 items-center gap-1">
        <Label htmlFor="question" className="sr-only">
          Question
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
            title={`Question ${question.id}`}
            className="-ms-2.5 me-auto min-h-11 min-w-0 border-transparent text-base font-semibold hover:bg-muted dark:bg-transparent"
          >
            <SelectValue className="min-w-0">
              <span className="truncate">{`Question ${question.id}`}</span>
            </SelectValue>
          </SelectTrigger>
          <SelectContent
            align="start"
            className="w-auto max-w-[min(var(--available-width),32rem)] min-w-(--anchor-width)"
          >
            {questions.map((item) => {
              const counts = progress.get(item.id);
              const ai = questionAiLabel(
                aiJob?.questions.find((run) => run.question_id === item.id)
              );
              return (
                <SelectItem
                  key={item.id}
                  value={item.id}
                  className="[&>div]:min-w-0 [&>div]:shrink"
                >
                  <span className="truncate">
                    Question {item.id} · {item.possible_points} points
                  </span>
                  {counts && (
                    <span className="ms-auto shrink-0 ps-4 text-muted-foreground tabular-nums">
                      {ai && `${ai} · `}
                      {counts.graded} of {counts.total} graded
                    </span>
                  )}
                </SelectItem>
              );
            })}
          </SelectContent>
        </Select>
        <LoadingIndicator loading={loadingQuestion} label="Loading question" />
        <Button
          variant="ghost"
          className="size-11"
          aria-label="Previous question"
          title={hint('Previous question', 'previousQuestion')}
          aria-keyshortcuts={shortcutAria('previousQuestion')}
          disabled={!previousQuestion || navigating}
          onClick={() =>
            previousQuestion && selectQuestion(previousQuestion.id)
          }
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Button
          variant="ghost"
          className="size-11"
          aria-label="Next question"
          title={hint('Next question', 'nextQuestion')}
          aria-keyshortcuts={shortcutAria('nextQuestion')}
          disabled={!nextQuestion || navigating}
          onClick={() => nextQuestion && selectQuestion(nextQuestion.id)}
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
      <QuestionAiStatus assignmentId={assignment.id} questionId={question.id} />
      <div className="grid gap-1.5">
        <div className="flex items-baseline justify-between gap-4 text-xs text-muted-foreground tabular-nums">
          <span>
            {graded} of {total} graded
          </span>
          {total > 0 && graded === total ? (
            <span className="flex items-baseline gap-1">
              <CircleCheck
                className="size-3.5 self-center text-chart-1"
                aria-hidden="true"
              />
              Complete
            </span>
          ) : (
            <span>{gradedPercentage}%</span>
          )}
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-chart-1/15"
          aria-hidden="true"
        >
          <div
            className="h-full rounded-full bg-chart-1"
            style={{ width: `${gradedPercentage}%` }}
          />
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeading
        className={submission && 'mb-4'}
        title="Grade"
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <AnonymousGrading />
            <AiDrafts assignmentId={assignment.id} />
            <GradingShortcuts />
          </div>
        }
      />
      {submission ? (
        <>
          {question ? (
            <AnswerView
              key={`${submission.id}/${question.id}`}
              assignmentId={assignment.id}
              navigating={navigating}
              onNext={
                ungraded ? () => selectSubmission(ungraded.id) : undefined
              }
              submissionId={submission.id}
              // The answer carries the question it is graded against, and is
              // refreshed when a rubric is edited; the route's copy is not.
              question={(data.answer && answer?.question) || question}
              answer={data.answer ? (answer ?? data.answer) : null}
              header={questionHeader}
            />
          ) : (
            <p className="flex-1 border-t py-8 text-muted-foreground">
              No questions available for this submission.
            </p>
          )}
          <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-2 border-t bg-background py-3 lg:static lg:pb-0">
            <Label htmlFor="submission" className="sr-only">
              Submission
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
                title={[identity?.name, identity?.detail]
                  .filter(Boolean)
                  .join(' · ')}
                className="min-h-11 min-w-0 flex-1 text-sm sm:w-72 sm:flex-none"
              >
                <SelectValue className="min-w-0">
                  <SubmissionLabel
                    submission={submission}
                    anonymous={anonymous}
                  />
                </SelectValue>
                <LoadingIndicator
                  loading={loadingSubmission}
                  label="Loading submission"
                />
              </SelectTrigger>
              <SelectContent
                align="start"
                side="top"
                className="w-auto max-w-[min(var(--available-width),32rem)] min-w-(--anchor-width)"
              >
                {submissions.map((item) => (
                  <SelectItem
                    key={item.id}
                    value={item.id}
                    className="[&>div]:min-w-0 [&>div]:shrink"
                  >
                    <SubmissionLabel submission={item} anonymous={anonymous} />
                    {question && (
                      <span className="ms-auto shrink-0 ps-4 text-muted-foreground tabular-nums">
                        {!(question.id in item.question_scores)
                          ? 'No response'
                          : item.question_scores[question.id] === null
                            ? 'Ungraded'
                            : `${item.question_scores[question.id]} / ${question.possible_points}`}
                      </span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-sm whitespace-nowrap text-muted-foreground tabular-nums">
              {index + 1} of {submissions.length}
            </span>
            <div className="flex w-full gap-2 sm:ms-auto sm:w-auto">
              <Button
                variant="outline"
                className="max-sm:w-11 max-sm:px-0"
                title={hint('Previous submission', 'previousSubmission')}
                aria-keyshortcuts={shortcutAria('previousSubmission')}
                disabled={!previous || navigating}
                onClick={() => previous && selectSubmission(previous.id)}
              >
                <ChevronLeft aria-hidden="true" />
                <span className="max-sm:sr-only">Previous</span>
              </Button>
              <Button
                variant="outline"
                className="max-sm:w-11 max-sm:px-0"
                title={hint('Next submission', 'nextSubmission')}
                aria-keyshortcuts={shortcutAria('nextSubmission')}
                disabled={!next || navigating}
                onClick={() => next && selectSubmission(next.id)}
              >
                <span className="max-sm:sr-only">Next</span>
                <ChevronRight aria-hidden="true" />
              </Button>
              {primary}
            </div>
          </div>
        </>
      ) : (
        <Empty className="border py-16">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <PenLine />
            </EmptyMedia>
            <EmptyTitle>Nothing to grade yet</EmptyTitle>
            <EmptyDescription>
              Answers show up here once scans are split into submissions.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              render={
                <Link
                  to="/assignments/$assignmentId/submissions"
                  params={{ assignmentId: assignment.id }}
                />
              }
            >
              Go to submissions
            </Button>
          </EmptyContent>
        </Empty>
      )}
    </div>
  );
}
