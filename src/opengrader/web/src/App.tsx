import { useEffect, useRef, type PropsWithChildren } from 'react';
import { useRouterState } from '@tanstack/react-router';
import { AssignmentLoading, AssignmentsLoading } from '@/components/loading';
import { AppHeader } from '@/components/app-header';

export function App({ children }: PropsWithChildren) {
  const initialPath = useRouterState({
    select: (state) =>
      state.matches.length === 0 ? state.location.pathname : null,
  });
  const main = useRef<HTMLElement>(null);
  const pathname = useRouterState({
    select: (state) => state.resolvedLocation?.pathname,
  });
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current !== pathname)
      main.current?.focus({ preventScroll: true });
    previousPath.current = pathname;
  }, [pathname]);

  return (
    <div className="min-h-svh text-sm leading-normal antialiased">
      <a
        className="absolute start-4 -top-24 z-10 bg-background p-3 focus:top-4"
        href="#content"
      >
        Skip to content
      </a>
      <AppHeader />
      <main
        className="mx-auto min-h-[calc(100svh-5rem)] max-w-[90rem] px-4 pt-8 pb-16 focus:outline-none sm:px-8 lg:px-14"
        id="content"
        ref={main}
        tabIndex={-1}
      >
        {children}
        {initialPath !== null &&
          (initialPath.startsWith('/grade/') ? (
            <AssignmentLoading />
          ) : (
            <AssignmentsLoading />
          ))}
      </main>
    </div>
  );
}
