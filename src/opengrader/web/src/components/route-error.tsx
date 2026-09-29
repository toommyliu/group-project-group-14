import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { useFolderSession } from '@/hooks/use-folder-session';
import { errorMessage } from '@/lib/error-message';
import { isMissingResource } from '@/lib/not-found';
import {
  Link,
  useRouter,
  type ErrorComponentProps,
  type NotFoundRouteProps,
} from '@tanstack/react-router';
import { PageHeading } from './page-heading';
import { Button, buttonVariants } from './ui/button';

export function RouteError({ error }: ErrorComponentProps) {
  const router = useRouter();
  return (
    <>
      <PageHeading
        title="Unable to load this page"
        description={errorMessage(error)}
      />
      <div
        className="flex min-h-80 items-start gap-4 border-t py-6"
        role="alert"
      >
        <Button variant="outline" onClick={() => void router.invalidate()}>
          Try again
        </Button>
        <Link
          to="/assignments"
          className="inline-flex min-h-11 items-center underline underline-offset-4"
        >
          All assignments
        </Link>
      </div>
    </>
  );
}

export function RouteNotFound({ data }: NotFoundRouteProps) {
  const folderSession = useFolderSession();
  const { assignment } = useAssignmentNavigation();
  const missing = isMissingResource(data) ? data : null;

  if (assignment) {
    const submission = missing?.kind === 'submission';
    return (
      <>
        <PageHeading
          title={submission ? 'Submission not found' : 'Page not found'}
          description={
            submission
              ? `${assignment.title} has no submission at this link. It may have been removed.`
              : `${assignment.title} has no page at this address. Check the link for typos.`
          }
        />
        <div className="flex min-h-80 items-start gap-4 border-t py-6">
          {submission && (
            <Link
              to="/assignments/$assignmentId/grade"
              params={{ assignmentId: assignment.id }}
              className={buttonVariants()}
            >
              Continue grading
            </Link>
          )}
          <Link
            to="/assignments/$assignmentId"
            params={{ assignmentId: assignment.id }}
            className={
              submission
                ? 'inline-flex min-h-11 items-center underline underline-offset-4'
                : buttonVariants()
            }
          >
            Assignment overview
          </Link>
        </div>
      </>
    );
  }

  return (
    <div className="mx-auto flex min-h-[calc(100svh-6rem)] w-full max-w-6xl flex-col items-start justify-center">
      <PageHeading
        className="mb-6 w-full"
        title={
          missing?.kind === 'assignment'
            ? 'Assignment not found'
            : 'Page not found'
        }
        description={
          missing?.kind === 'assignment'
            ? 'It may have been deleted, or the link may be out of date.'
            : 'There’s no page at this address. Check the link for typos.'
        }
      />
      {folderSession ? (
        <Link
          to="/assignments/$assignmentId"
          params={{ assignmentId: folderSession.assignment_id }}
          className={buttonVariants({ size: 'lg' })}
        >
          Back to assignment
        </Link>
      ) : (
        <Link to="/assignments" className={buttonVariants({ size: 'lg' })}>
          All assignments
        </Link>
      )}
    </div>
  );
}
