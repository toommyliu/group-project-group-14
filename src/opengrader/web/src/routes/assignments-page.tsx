import {
  Link,
  useRouter,
  useRouterState,
  useMatchRoute,
} from '@tanstack/react-router';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import { assignmentsQuery } from '@/api/queries';
import { indexRoute } from '@/router';
import { PageHeading } from '@/components/page-heading';
import { Button } from '@/components/ui/button';

export default function AssignmentsPage() {
  const assignments = indexRoute.useLoaderData();
  const router = useRouter();
  const matchRoute = useMatchRoute();
  const loading = useRouterState({ select: (state) => state.isLoading });
  const pendingAssignment =
    loading && matchRoute({ to: '/grade/$assignmentId', pending: true });
  return (
    <>
      <PageHeading
        title="Assignments"
        description="Choose an assignment to review its responses."
      />
      {assignments.length ? (
        <ul className="border-t">
          {assignments.map((assignment) => (
            <li key={assignment.id}>
              <Link
                to="/grade/$assignmentId"
                params={{ assignmentId: assignment.id }}
                className="group flex min-h-25 items-center justify-between gap-4 border-b py-6 hover:bg-muted/40 motion-safe:transition-colors motion-safe:duration-150"
              >
                <span className="min-w-0 break-words">
                  <span className="block text-base font-medium">
                    {assignment.title}
                  </span>
                  <span className="mt-1 block text-sm text-muted-foreground">
                    {assignment.id}
                  </span>
                </span>
                <span
                  className="me-3 flex size-4 shrink-0 items-center text-muted-foreground group-hover:text-foreground"
                  role="status"
                >
                  {pendingAssignment &&
                  pendingAssignment.assignmentId === assignment.id ? (
                    <>
                      <LoaderCircle
                        className="size-4 motion-safe:animate-spin"
                        aria-hidden="true"
                      />
                      <span className="sr-only">
                        Loading {assignment.title}
                      </span>
                    </>
                  ) : (
                    <ArrowRight className="size-4" aria-hidden="true" />
                  )}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <div className="min-h-80 space-y-4 border-t py-8">
          <h2 className="text-base font-semibold">No assignments yet</h2>
          <p>
            Ask the instructor to add a prepared assignment and restart the
            server.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              router.options.context.queryClient.removeQueries({
                queryKey: assignmentsQuery.queryKey,
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
