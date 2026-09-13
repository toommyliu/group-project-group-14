import { Link, useRouterState } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { ModeToggle } from './mode-toggle';

export function AppHeader() {
  const assignment = useRouterState({
    select: (state) =>
      state.matches.find((match) => match.routeId === '/grade/$assignmentId')
        ?.loaderData?.assignment,
  });
  return (
    <header className="border-b">
      <div className="mx-auto flex min-h-16 max-w-[90rem] items-center gap-3 px-4 sm:gap-6 sm:px-8 lg:px-14">
        <Link
          to="/"
          className="flex min-h-11 shrink-0 items-center text-base font-semibold"
        >
          Opengrader
        </Link>
        <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
          <ol className="flex min-w-0 items-center gap-2 text-sm">
            <li>
              <Link
                to="/"
                className="flex min-h-11 items-center text-muted-foreground hover:text-foreground"
                aria-current={assignment ? undefined : 'page'}
              >
                {assignment && (
                  <ChevronLeft
                    className="size-4 sm:hidden"
                    aria-hidden="true"
                  />
                )}
                <span
                  className={assignment ? 'sr-only sm:not-sr-only' : undefined}
                >
                  Assignments
                </span>
              </Link>
            </li>
            {assignment && (
              <>
                <li aria-hidden="true" className="hidden sm:block">
                  <ChevronRight className="size-4 text-muted-foreground" />
                </li>
                <li
                  className="min-w-0 max-w-64 truncate sm:max-w-96"
                  aria-current="page"
                  title={assignment.title}
                >
                  {assignment.title}
                </li>
              </>
            )}
          </ol>
        </nav>
        <ModeToggle />
      </div>
    </header>
  );
}
