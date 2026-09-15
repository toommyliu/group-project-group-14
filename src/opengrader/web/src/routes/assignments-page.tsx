import { Link, useRouter, useRouterState } from '@tanstack/react-router';
import { ArrowRight, LoaderCircle } from 'lucide-react';
import { assignmentsQuery } from '@/api/queries';
import { indexRoute } from '@/router';
import { PageHeading } from '@/components/page-heading';
import { Button, buttonVariants } from '@/components/ui/button';

export default function AssignmentsPage() {
  const assignments = indexRoute.useLoaderData();
  const router = useRouter();
  const pendingAssignment = useRouterState({
    select: (state) =>
      state.isLoading && state.location.pathname === '/assignment'
        ? state.location.search.assignment
        : undefined,
  });
  return (
    <>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <PageHeading className="mb-0 flex-1" title="Assignments" />
        <Link
          to="/new-assignment"
          className={buttonVariants({
            className: 'shrink-0 self-start sm:self-auto',
          })}
        >
          Create assignment
        </Link>
      </div>
      {assignments.length ? (
        <ul className="border-t">
          {assignments.map((assignment) => (
            <li key={assignment.id}>
              <Link
                to="/assignment"
                search={{ assignment: assignment.id }}
                className="group flex min-h-14 items-center justify-between gap-4 border-b py-2 hover:bg-muted/40 motion-safe:transition-colors motion-safe:duration-150"
              >
                <span className="min-w-0 break-words">
                  <span
                    className="block truncate text-base font-medium"
                    title={assignment.title}
                  >
                    {assignment.title}
                  </span>
                </span>
                <span
                  className="me-3 flex size-4 shrink-0 items-center text-muted-foreground group-hover:text-foreground"
                  role="status"
                >
                  {pendingAssignment && pendingAssignment === assignment.id ? (
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
          <p>Create an assignment from a QA answer key to get started.</p>
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
