import { Fragment } from 'react';
import {
  Link,
  useMatchRoute,
  useRouter,
  useRouterState,
} from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, CircleCheck, LoaderCircle, PenLine } from 'lucide-react';
import {
  assignmentSummariesQuery,
  type AssignmentSummary,
} from '@/api/queries';
import { assignmentsRoute } from '@/router';
import { ErrorAlert } from '@/components/error-alert';
import { PageHeading } from '@/components/page-heading';
import { Button, buttonVariants } from '@/components/ui/button';

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 0,
});

function count(value: number, singular: string, plural = `${singular}s`) {
  return `${number.format(value)} ${value === 1 ? singular : plural}`;
}

// Floor so an unfinished assignment never rounds up to 100%. Scaling before
// dividing keeps exact percentages whole: (29 / 100) * 100 is 28.999….
function gradedFraction(graded: number, total: number) {
  return total ? Math.floor((graded * 100) / total) / 100 : 0;
}

function isComplete(assignment: AssignmentSummary) {
  return (
    assignment.submission_count > 0 &&
    assignment.graded_submission_count === assignment.submission_count
  );
}

export default function AssignmentsPage() {
  return <AssignmentsList loaded={assignmentsRoute.useLoaderData()} />;
}

export function AssignmentsList({ loaded }: { loaded: AssignmentSummary[] }) {
  const query = useQuery({
    ...assignmentSummariesQuery,
    initialData: loaded,
  });
  const assignments = query.data;
  const router = useRouter();
  const navigating = useRouterState({ select: (state) => state.isLoading });
  const matchRoute = useMatchRoute();
  const pendingMatch =
    navigating &&
    matchRoute({ to: '/assignments/$assignmentId', pending: true });
  const pendingAssignment = pendingMatch
    ? pendingMatch.assignmentId
    : undefined;
  return (
    <>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <PageHeading className="mb-0 flex-1" title="Assignments" />
        <Link
          to="/assignments/new"
          className={buttonVariants({
            className: 'shrink-0 self-start sm:self-auto',
          })}
        >
          Create assignment
        </Link>
      </div>
      {query.isError && (
        <ErrorAlert
          className="mb-6"
          title="Assignments could not be refreshed. Showing the last loaded progress."
          error={query.error}
        >
          <Button variant="outline" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </ErrorAlert>
      )}
      {assignments.length ? (
        <div className="@container">
          <WorkspaceSummary assignments={assignments} />
          <ul className="grid grid-cols-1 gap-4 @xl:grid-cols-2 @5xl:grid-cols-3">
            {assignments.map((assignment) => (
              <li key={assignment.id}>
                <AssignmentCard
                  assignment={assignment}
                  pending={pendingAssignment === assignment.id}
                />
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="min-h-80 space-y-4 border-t py-8">
          <h2 className="text-base font-semibold">No assignments yet</h2>
          <p>
            Create an assignment from the PDF students write on to get started.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              router.options.context.queryClient.removeQueries({
                queryKey: assignmentSummariesQuery.queryKey,
                exact: true,
              });
              void router.invalidate();
            }}
          >
            Refresh assignments
          </Button>
        </div>
      )}
    </>
  );
}

function WorkspaceSummary({
  assignments,
}: {
  assignments: AssignmentSummary[];
}) {
  let submissions = 0;
  let gradedSubmissions = 0;
  let responses = 0;
  let gradedResponses = 0;
  for (const item of assignments) {
    submissions += item.submission_count;
    gradedSubmissions += item.graded_submission_count;
    responses += item.response_count;
    gradedResponses += item.graded_response_count;
  }
  // Assignments without submissions have nothing to grade yet.
  const gradable = assignments.filter((item) => item.submission_count);
  const metrics = [
    {
      label: 'Assignments',
      total: assignments.length,
      left: gradable.filter((item) => !isComplete(item)).length,
      gradable: gradable.length,
    },
    {
      label: 'Submissions',
      total: submissions,
      left: submissions - gradedSubmissions,
      gradable: submissions,
    },
    {
      label: 'Responses',
      total: responses,
      left: responses - gradedResponses,
      gradable: responses,
    },
  ];
  return (
    <dl className="mb-8 grid grid-cols-2 gap-x-6 gap-y-5 border-y py-5 tabular-nums @lg:grid-cols-3">
      {metrics.map((metric) => (
        <div key={metric.label} className="min-w-0">
          <dt className="text-sm text-muted-foreground">{metric.label}</dt>
          <dd className="mt-2 text-2xl font-semibold tracking-tight">
            {number.format(metric.total)}
          </dd>
          <dd className="mt-1 text-sm text-muted-foreground">
            {!metric.gradable
              ? 'Nothing to grade yet'
              : metric.left
                ? `${number.format(metric.left)} left to grade`
                : 'All graded'}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function AssignmentCard({
  assignment,
  pending,
}: {
  assignment: AssignmentSummary;
  pending: boolean;
}) {
  const complete = isComplete(assignment);
  const fraction = gradedFraction(
    assignment.graded_response_count,
    assignment.response_count
  );
  const details = [
    assignment.question_count === null
      ? 'Questions vary by version'
      : count(assignment.question_count, 'question'),
    assignment.possible_points !== null &&
      count(assignment.possible_points, 'point'),
    assignment.version_count > 0 && count(assignment.version_count, 'version'),
    count(assignment.submission_count, 'submission'),
  ].filter(Boolean);
  const gradingLabel =
    assignment.response_count === 0
      ? undefined
      : assignment.graded_response_count === 0
        ? 'Start grading'
        : assignment.graded_response_count < assignment.response_count
          ? 'Continue grading'
          : 'Review grades';
  return (
    <article className="group relative flex h-full flex-col gap-5 rounded-lg border p-5 tabular-nums has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-offset-2 has-[a:focus-visible]:outline-foreground has-[a:hover]:bg-muted/40 motion-safe:transition-colors motion-safe:duration-150">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2
            className="truncate text-base font-medium"
            title={assignment.title}
          >
            <Link
              to="/assignments/$assignmentId"
              params={{ assignmentId: assignment.id }}
              className="outline-none after:absolute after:inset-0"
            >
              {assignment.title}
            </Link>
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {details.map((detail, index) => (
              <Fragment key={index}>
                {index > 0 && ' · '}
                <span className="whitespace-nowrap">{detail}</span>
              </Fragment>
            ))}
          </p>
        </div>
        <span
          className="flex size-6 shrink-0 items-center justify-center text-muted-foreground group-has-[a:hover]:text-foreground"
          role="status"
        >
          {pending ? (
            <>
              <LoaderCircle
                className="size-4 motion-safe:animate-spin"
                aria-hidden="true"
              />
              <span className="sr-only">Loading {assignment.title}</span>
            </>
          ) : (
            <ArrowRight className="size-4" aria-hidden="true" />
          )}
        </span>
      </div>

      <div className="space-y-2">
        {assignment.submission_count === 0 ? (
          <p className="text-sm text-muted-foreground">No submissions yet</p>
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-4 text-sm">
              <span>
                {number.format(assignment.graded_response_count)} of{' '}
                {count(assignment.response_count, 'response')} graded
              </span>
              {complete ? (
                <span className="flex items-baseline gap-1.5 font-medium">
                  <CircleCheck
                    className="size-4 self-center text-chart-1"
                    aria-hidden="true"
                  />
                  Complete
                </span>
              ) : (
                <span className="font-medium">{percent.format(fraction)}</span>
              )}
            </div>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-chart-1/15"
              aria-hidden="true"
            >
              <div
                className="h-full rounded-full bg-chart-1"
                style={{ width: `${fraction * 100}%` }}
              />
            </div>
          </>
        )}
      </div>

      <dl className="mt-auto flex items-baseline justify-between gap-4 border-t pt-4 text-sm">
        <dt className="shrink-0 text-muted-foreground">Mean score</dt>
        <dd className="text-end">
          {assignment.average_percent !== null ? (
            <>
              <span className="font-medium">
                {percent.format(assignment.average_percent / 100)}
              </span>
              {!complete && (
                <span className="text-muted-foreground">
                  {' '}
                  from {count(assignment.graded_submission_count, 'submission')}
                </span>
              )}
            </>
          ) : assignment.graded_submission_count ? (
            '—'
          ) : (
            <span className="text-muted-foreground">Not graded yet</span>
          )}
        </dd>
      </dl>
      {gradingLabel && (
        <Link
          to="/assignments/$assignmentId/grade"
          params={{ assignmentId: assignment.id }}
          search={{}}
          className={buttonVariants({
            variant: complete ? 'outline' : 'default',
            className: 'relative z-10 w-full',
          })}
        >
          <PenLine aria-hidden="true" />
          {gradingLabel}
        </Link>
      )}
    </article>
  );
}
