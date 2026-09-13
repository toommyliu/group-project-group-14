import { useRef, type PropsWithChildren } from 'react';
import { useRouterState } from '@tanstack/react-router';
import { AssignmentLoading, AssignmentsLoading } from '@/components/loading';
import { AppHeader } from '@/components/app-header';

export function App({ children }: PropsWithChildren) {
  const initialPath = useRouterState({
    select: (state) =>
      state.matches.length === 0 ? state.location.pathname : null,
  });
  const main = useRef<HTMLElement>(null);

  return (
    <div className="min-h-svh text-sm leading-normal antialiased">
      <AppHeader />
      <main
        className="mx-auto min-h-[calc(100svh-4rem)] max-w-[90rem] px-4 pt-8 pb-16 focus:outline-none sm:px-8 lg:px-14"
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
