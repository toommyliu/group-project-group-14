import { useContext } from 'react';
import { WorkspacePages } from '@/lib/workspace-pages';
import { Link, useMatchRoute } from '@tanstack/react-router';
import {
  BookOpen,
  ChartColumn,
  Files,
  LayoutGrid,
  ScanLine,
} from 'lucide-react';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { useFolderSession } from '@/hooks/use-folder-session';
import { ModeToggle } from './mode-toggle';
import { AuthControl } from './auth-control';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarResizeHandle,
  useSidebar,
  SidebarTrigger,
} from './ui/sidebar';

const menuButtonClass =
  'min-h-11 px-4 text-sm transition-none group-data-[collapsible=icon]:h-11! group-data-[collapsible=icon]:w-12! group-data-[collapsible=icon]:px-4! focus-visible:ring-2 focus-visible:ring-inset';

export function AppSidebar() {
  const { assignment } = useAssignmentNavigation();
  const folderSession = useFolderSession();
  const matchRoute = useMatchRoute();
  const pages = useContext(WorkspacePages);
  const gradingPage = pages.find(
    (page) =>
      page.path === '/assignments/$assignmentId/submissions' &&
      page.data.assignment.id === assignment?.id
  );
  const gradingSelection =
    gradingPage?.path === '/assignments/$assignmentId/submissions'
      ? {
          submission: gradingPage.data.submission?.id,
          question: gradingPage.data.question?.id,
        }
      : {};
  const { setOpenMobile, isMobile, open, openMobile } = useSidebar();
  const browsing =
    Boolean(matchRoute({ to: '/assignments', pending: true })) ||
    Boolean(matchRoute({ to: '/assignments/new', pending: true }));
  const expanded = isMobile ? openMobile : open;
  const close = () => setOpenMobile(false);
  const destinations = (
    [
      {
        to: '/assignments/$assignmentId',
        title: 'Overview',
        icon: BookOpen,
      },
      {
        to: '/assignments/$assignmentId/scans',
        title: 'Scans',
        icon: ScanLine,
      },
      {
        to: '/assignments/$assignmentId/submissions',
        title: 'Submissions',
        icon: Files,
      },
      {
        to: '/assignments/$assignmentId/statistics',
        title: 'Statistics',
        icon: ChartColumn,
      },
      // {
      //   to: '/assignments/$assignmentId/terminals',
      //   title: 'Terminals',
      //   icon: TerminalSquare,
      // },
    ] as const
  ).filter(
    // A folder session grades one class folder: no scans or terminals to manage.
    (destination) => !folderSession || destination.title !== 'Scans'
  );
  return (
    <>
      <Sidebar
        id="assignment-sidebar"
        collapsible="icon"
        aria-label="Navigation"
      >
        <SidebarHeader className="h-14 shrink-0 flex-row items-center gap-0 overflow-hidden border-b border-sidebar-border px-2">
          <SidebarTrigger
            className="h-11 w-12 shrink-0 active:translate-y-0!"
            aria-expanded={expanded}
          />
          {folderSession ? (
            <Link
              to="/assignments/$assignmentId"
              params={{ assignmentId: folderSession.assignment_id }}
              onClick={close}
              className="-ms-2 min-w-0 truncate text-sm font-medium group-data-[collapsible=icon]:hidden hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >
              Opengrader
            </Link>
          ) : (
            <Link
              to="/"
              onClick={close}
              className="-ms-2 min-w-0 truncate text-sm font-medium group-data-[collapsible=icon]:hidden hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >
              Opengrader
            </Link>
          )}
        </SidebarHeader>
        <SidebarContent className="overflow-x-hidden pt-2">
          {!folderSession && (
            <nav aria-label="Workspace" className="w-full">
              <SidebarGroup className="px-2 py-0">
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      className={menuButtonClass}
                      tooltip="All assignments"
                      aria-label="All assignments"
                      isActive={browsing}
                      render={<Link to="/assignments" onClick={close} />}
                      aria-current={browsing ? 'page' : undefined}
                    >
                      <LayoutGrid aria-hidden="true" />
                      <span className="group-data-[collapsible=icon]:hidden">
                        All assignments
                      </span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroup>
            </nav>
          )}
          {assignment && (
            <nav aria-label={assignment.title} className="w-full">
              <SidebarGroup className="px-2 pt-2 pb-0">
                {/* Keep the slot's height in both states so the icons below never move. */}
                <div
                  title={assignment.title}
                  className="flex h-6 shrink-0 items-center px-4 text-xs font-medium text-sidebar-foreground/70 select-none group-data-[collapsible=icon]:px-2"
                >
                  <span className="truncate group-data-[collapsible=icon]:hidden">
                    {assignment.title}
                  </span>
                  <span
                    aria-hidden="true"
                    className="hidden h-px w-full bg-sidebar-border group-data-[collapsible=icon]:block"
                  />
                </div>
                <SidebarGroupContent className="mt-1">
                  <SidebarMenu>
                    {destinations.map(({ to, title, icon: Icon }) => {
                      const active = Boolean(matchRoute({ to, pending: true }));
                      return (
                        <SidebarMenuItem key={to}>
                          <SidebarMenuButton
                            className={menuButtonClass}
                            tooltip={title}
                            aria-label={title}
                            isActive={active}
                            render={
                              <Link
                                to={to}
                                params={{ assignmentId: assignment.id }}
                                search={
                                  to ===
                                  '/assignments/$assignmentId/submissions'
                                    ? gradingSelection
                                    : {}
                                }
                                onClick={close}
                              />
                            }
                            aria-current={active ? 'page' : undefined}
                          >
                            <Icon aria-hidden="true" />
                            <span className="group-data-[collapsible=icon]:hidden">
                              {title}
                            </span>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      );
                    })}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            </nav>
          )}
        </SidebarContent>
        <SidebarFooter className="flex-row items-center gap-1 border-t border-sidebar-border px-2 py-2 group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:items-start">
          <div className="min-w-0 flex-1 group-data-[collapsible=icon]:flex-none">
            <AuthControl variant="sidebar" compact={!expanded} />
          </div>
          <ModeToggle
            side={expanded ? 'top' : 'right'}
            align="end"
            className="h-11 w-12 shrink-0 border-0 hover:bg-sidebar-accent aria-expanded:bg-sidebar-accent"
          />
        </SidebarFooter>
        <SidebarResizeHandle />
      </Sidebar>
    </>
  );
}
