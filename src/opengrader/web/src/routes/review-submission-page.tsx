import type { ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  FileText,
  PenLine,
} from 'lucide-react';
import { cn } from 'cn';
import { workspaceUrl } from '@/api/client';
import {
  assignmentQAQuery,
  feedbackJobsQuery,
  submissionAnswersQuery,
  submissionsQuery,
  type Answer,
} from '@/api/queries';
import { reviewSubmissionRoute } from '@/router';
import { percentText, scoreText, versionColumns } from '@/lib/grade-columns';
import { submissionIdentity } from '@/lib/submission-identity';
import { usePreferences } from '@/stores/preferences';
import { AnonymousGrading } from '@/components/anonymous-grading';
import { PageHeading } from '@/components/page-heading';
import { GradingText } from '@/components/grading-text';
import { FeedbackPdfButton } from '@/components/feedback-pdf-button';
import { Button, buttonVariants } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

export default function ReviewSubmissionPage() {
  const anonymous = usePreferences((state) => state.anonymousGrading);
  const loaded = reviewSubmissionRoute.useLoaderData();
  const { assignment, submissionId } = loaded;
  const submissions = useQuery({
    ...submissionsQuery(assignment.id),
    initialData: loaded.submissions,
  });
  const qa = useQuery({
    ...assignmentQAQuery(assignment.id),
    initialData: loaded.qa,
  });
  const answers = useQuery({
    ...submissionAnswersQuery(assignment.id, submissionId),
    initialData: loaded.answers,
  });
  const jobs = useQuery(feedbackJobsQuery);

  const index = submissions.data.findIndex((item) => item.id === submissionId);
  const submission = submissions.data[index];
  if (!submission)
    return (
      <>
        <BackLink assignmentId={assignment.id} />
        <PageHeading title="Submission removed" />
        <p className="text-muted-foreground">
          This submission is no longer in the assignment.
        </p>
      </>
    );
  const previous = submissions.data[index - 1];
  const next = submissions.data[index + 1];
  const identity = submissionIdentity(submission, anonymous);

  const columns =
    versionColumns(qa.data.document).get(submission.version ?? '') ?? [];
  const byId = new Map(
    answers.data.map((answer) => [answer.question.id, answer])
  );
  const items = [
    ...columns.map((column) => ({
      label: column.label,
      points: column.points,
      answer: byId.get(column.id),
      questionId: column.id,
    })),
    ...answers.data
      .filter(
        (answer) => !columns.some((column) => column.id === answer.question.id)
      )
      .map((answer) => ({
        label: answer.question.id,
        points: answer.question.possible_points,
        answer,
        questionId: answer.question.id,
      })),
  ];
  const graded = items.filter(
    (item) =>
      item.answer?.points_awarded !== null &&
      item.answer?.points_awarded !== undefined
  );
  const total = graded.reduce(
    (sum, item) => sum + (item.answer?.points_awarded ?? 0),
    0
  );
  const possible = items.reduce((sum, item) => sum + item.points, 0);
  const complete = items.length > 0 && graded.length === items.length;
  const job = jobs.data?.find(
    (item) =>
      item.assignment_id === assignment.id &&
      item.submission_id === submissionId
  );

  return (
    <>
      <div className="mb-4 flex items-center gap-2">
        <BackLink assignmentId={assignment.id} />
        <div className="ms-auto flex gap-2">
          <StudentStep
            assignmentId={assignment.id}
            submissionId={previous?.id}
            label="Previous student"
          >
            <ChevronLeft aria-hidden="true" />
          </StudentStep>
          <StudentStep
            assignmentId={assignment.id}
            submissionId={next?.id}
            label="Next student"
          >
            <ChevronRight aria-hidden="true" />
          </StudentStep>
        </div>
      </div>
      <PageHeading
        title={identity.name}
        description={[
          identity.sid && `SID ${identity.sid}`,
          `Submission #${submission.id}`,
          submission.version && `Version ${submission.version}`,
        ]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <div className="flex flex-wrap items-center gap-1">
            <AnonymousGrading />
            {!anonymous && (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <a
                      href={workspaceUrl(
                        `/api/assignments/${encodeURIComponent(assignment.id)}/submissions/${submission.id}/pdf`
                      )}
                      target="_blank"
                      rel="noreferrer"
                      aria-label="Open the scanned PDF"
                      className={cn(
                        buttonVariants({ variant: 'ghost', size: 'icon' }),
                        'size-11'
                      )}
                    />
                  }
                >
                  <FileText aria-hidden="true" />
                </TooltipTrigger>
                <TooltipContent>Open the scanned PDF</TooltipContent>
              </Tooltip>
            )}
            <FeedbackPdfButton
              assignmentId={assignment.id}
              submissionId={submission.id}
              job={job}
              graded={graded.length > 0}
            />
            <Button
              variant="outline"
              render={
                <Link
                  to="/assignments/$assignmentId/grade"
                  params={{ assignmentId: assignment.id }}
                  search={{ submission: submission.id }}
                />
              }
            >
              <PenLine aria-hidden="true" />
              Grade
            </Button>
          </div>
        }
      />

      <div className="mb-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-2xl font-semibold tabular-nums">
          {scoreText(total) || '0'}
          <span className="text-muted-foreground"> / {possible}</span>
        </p>
        <p className="text-muted-foreground">
          {complete
            ? percentText(total, possible)
            : `${graded.length} of ${items.length} questions graded. The bulk feedback download skips this student until every question is graded.`}
        </p>
      </div>

      <ol className="grid">
        {items.map((item) => (
          <QuestionResult
            key={item.questionId}
            assignmentId={assignment.id}
            submissionId={submission.id}
            label={item.label}
            points={item.points}
            questionId={item.questionId}
            answer={item.answer}
          />
        ))}
      </ol>
    </>
  );
}

function BackLink({ assignmentId }: { assignmentId: string }) {
  return (
    <Button
      variant="ghost"
      className="-ms-3"
      render={
        <Link
          to="/assignments/$assignmentId/review"
          params={{ assignmentId }}
        />
      }
    >
      <ArrowLeft aria-hidden="true" />
      Review grades
    </Button>
  );
}

function StudentStep({
  assignmentId,
  submissionId,
  label,
  children,
}: {
  assignmentId: string;
  submissionId: number | undefined;
  label: string;
  children: ReactNode;
}) {
  if (submissionId === undefined)
    return (
      <Button variant="outline" className="size-11" disabled aria-label={label}>
        {children}
      </Button>
    );
  return (
    <Button
      variant="outline"
      className="size-11"
      aria-label={label}
      title={label}
      render={
        <Link
          to="/assignments/$assignmentId/review/$submissionId"
          params={{ assignmentId, submissionId: String(submissionId) }}
        />
      }
    >
      {children}
    </Button>
  );
}

function QuestionResult({
  assignmentId,
  submissionId,
  label,
  points,
  questionId,
  answer,
}: {
  assignmentId: string;
  submissionId: number;
  label: string;
  points: number;
  questionId: string;
  answer: Answer | undefined;
}) {
  const headingId = `question-${questionId}`;
  const awarded = answer?.points_awarded ?? null;
  const rubric = answer
    ? answer.rubric_labels.map((label) => ({
        label,
        delta: answer.question.rubric.find((item) => item.label === label)
          ?.points_delta,
      }))
    : [];
  return (
    <li
      aria-labelledby={headingId}
      className="grid gap-4 border-t py-6 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-8"
    >
      <div className="min-w-0 lg:order-2">
        <div className="flex min-h-11 items-center gap-3">
          <h2 id={headingId} className="text-base font-semibold">
            Question {label}
          </h2>
          <span
            className={cn(
              'tabular-nums',
              awarded === null && 'text-muted-foreground'
            )}
          >
            {awarded === null
              ? `Not graded · ${points} points`
              : `${scoreText(awarded)} / ${points}`}
          </span>
          <Button
            variant="ghost"
            className="ms-auto"
            aria-label={`Grade question ${label}`}
            render={
              <Link
                to="/assignments/$assignmentId/grade"
                params={{ assignmentId }}
                search={{ submission: submissionId, question: questionId }}
              />
            }
          >
            Grade
          </Button>
        </div>
        {rubric.length > 0 && (
          <ul className="mt-2 grid gap-1 text-sm">
            {rubric.map((item) => (
              <li key={item.label} className="flex gap-2">
                <span className="w-10 shrink-0 text-end text-muted-foreground tabular-nums">
                  {item.delta === undefined
                    ? ''
                    : item.delta > 0
                      ? `+${item.delta}`
                      : String(item.delta)}
                </span>
                <span>{item.label}</span>
              </li>
            ))}
          </ul>
        )}
        {answer?.analysis && (
          <div className="mt-4">
            <h3 className="mb-1 text-sm text-muted-foreground">Comment</h3>
            <GradingText>{answer.analysis}</GradingText>
          </div>
        )}
      </div>
      <div className="min-w-0 lg:order-1">
        {!answer ? (
          <p className="text-muted-foreground">No response recorded.</p>
        ) : answer.response_image_url ? (
          <a
            href={workspaceUrl(answer.response_image_url)}
            target="_blank"
            rel="noreferrer"
            className="block border bg-muted/30 p-2"
          >
            <img
              src={workspaceUrl(answer.response_image_url)}
              width={answer.response_image_width ?? undefined}
              height={answer.response_image_height ?? undefined}
              loading="lazy"
              alt={`Scanned answer to question ${label}`}
              className="mx-auto h-auto max-h-[60svh] w-auto max-w-full object-contain"
            />
          </a>
        ) : answer.response_text ? (
          <GradingText>{answer.response_text}</GradingText>
        ) : (
          <p className="text-muted-foreground">No response recorded.</p>
        )}
      </div>
    </li>
  );
}
