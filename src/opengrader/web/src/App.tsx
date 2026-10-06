import { workspaceBase } from '@/api/client';
import type { CSSProperties, PropsWithChildren } from 'react';
import { useMatchRoute, useRouterState } from '@tanstack/react-router';
import { cn } from 'cn';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { useFolderSession } from '@/hooks/use-folder-session';
import { AppSidebar } from '@/components/app-sidebar';
import { FolderSessionBanner } from '@/components/folder-session-banner';
import { CourseBreadcrumb } from '@/components/course-breadcrumb';
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { TooltipProvider } from '@/components/ui/tooltip';
import { setPreference, usePreferences } from '@/stores/preferences';
import { useSidebarWidth } from '@/stores/sidebar';

function MobileSidebarTrigger() {
  const { openMobile } = useSidebar();

  return (
    <SidebarTrigger
      className="-ms-3.5 size-11 shrink-0 active:translate-y-0! md:hidden"
      aria-label="Open navigation"
      aria-expanded={openMobile}
    />
  );
}

export function App({ children }: PropsWithChildren) {
  const sidebarExpanded = usePreferences((state) => state.sidebarExpanded);
  const sidebarWidth = useSidebarWidth();
  const { assignment, pathname } = useAssignmentNavigation();
  const folderSession = useFolderSession();
  const matchRoute = useMatchRoute();
  const overview =
    Boolean(matchRoute({ to: '/assignments/new' })) ||
    (assignment &&
      (Boolean(matchRoute({ to: '/assignments/$assignmentId' })) ||
        Boolean(matchRoute({ to: '/assignments/$assignmentId/outline' }))));
  const nothingToGrade = useRouterState({
    select: ({ matches }) =>
      matches.some(
        (match) =>
          match.routeId === '/assignments/$assignmentId/grade' &&
          match.loaderData !== undefined &&
          !match.loaderData.submission
      ),
  });
  const grading =
    assignment &&
    !nothingToGrade &&
    Boolean(matchRoute({ to: '/assignments/$assignmentId/grade' }));
  const scans =
    assignment &&
    Boolean(matchRoute({ to: '/assignments/$assignmentId/scans' }));
  const courses = Boolean(matchRoute({ to: '/courses' }));
  const scrollingTable =
    Boolean(matchRoute({ to: '/people' })) ||
    (assignment &&
      (Boolean(matchRoute({ to: '/assignments/$assignmentId/submissions' })) ||
        Boolean(matchRoute({ to: '/assignments/$assignmentId/review' }))));
  const notFound = useRouterState({
    select: ({ matches }) =>
      matches.some((match) => match.status === 'notFound' || match._notFound),
  });
  const standalone =
    !assignment && ((!workspaceBase && pathname === '/') || notFound);
  return (
    <TooltipProvider>
      <SidebarProvider
        open={sidebarExpanded}
        onOpenChange={(open) => setPreference('sidebarExpanded', open)}
        style={
          {
            '--sidebar-width': `${sidebarWidth}px`,
          } as CSSProperties
        }
        className={cn(
          overview || scrollingTable
            ? 'h-svh min-h-0 overflow-hidden'
            : grading
              ? 'lg:h-svh lg:min-h-0 lg:overflow-hidden'
              : standalone
                ? 'block'
                : '',
          'text-sm leading-normal antialiased has-data-[resizing=true]:select-none has-data-[resizing=true]:[&_[data-slot=sidebar-container]]:transition-none has-data-[resizing=true]:[&_[data-slot=sidebar-gap]]:transition-none'
        )}
      >
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:start-4 focus:top-4 focus:z-[100] focus:bg-background focus:p-3 focus:outline-2"
        >
          Skip to content
        </a>
        {!standalone && <AppSidebar />}
        <SidebarInset
          className={
            overview || scrollingTable
              ? 'h-full min-h-0 min-w-0 overflow-hidden'
              : grading
                ? 'min-w-0 lg:h-full lg:min-h-0 lg:overflow-hidden'
                : standalone
                  ? 'min-h-svh min-w-0'
                  : 'min-w-0'
          }
        >
          <div
            className={
              !standalone
                ? `mx-auto w-full max-w-[90rem] px-4 pt-4 ${overview ? 'flex h-full min-h-0 flex-col overflow-hidden' : scrollingTable ? 'flex h-full min-h-0 flex-col overflow-hidden pb-4 md:pb-8' : grading ? 'pb-16 lg:flex lg:h-full lg:min-h-0 lg:flex-col lg:overflow-hidden lg:pb-4' : scans || courses || nothingToGrade ? 'flex flex-1 flex-col pb-4 md:pb-8' : 'pb-16'} focus:outline-none sm:px-8 md:pt-8`
                : 'mx-auto w-full max-w-[90rem] px-4 pt-8 pb-16 focus:outline-none sm:px-8 lg:px-14'
            }
            id="content"
            tabIndex={-1}
          >
            {!standalone && (
              <div
                className={cn(
                  'flex min-w-0 shrink-0 items-center gap-2',
                  folderSession ? 'mb-6 md:hidden' : 'mb-4'
                )}
              >
                <MobileSidebarTrigger />
                {!folderSession && <CourseBreadcrumb />}
              </div>
            )}
            {folderSession && assignment && (
              <FolderSessionBanner session={folderSession} />
            )}
            {children}
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}
