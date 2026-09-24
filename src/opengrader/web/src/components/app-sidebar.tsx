import { useContext } from 'react';
import { WorkspacePages } from '@/lib/workspace-pages';
import { Link, useMatchRoute } from '@tanstack/react-router';
import { BookOpen, ChartColumn, Files, ScanLine } from 'lucide-react';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
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

export function AppSidebar() {
  const { assignment } = useAssignmentNavigation();
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
  const expanded = isMobile ? openMobile : open;
  const close = () => setOpenMobile(false);
  const destinations = [
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
  ] as const;
  return (
    <>
      <Sidebar
        id="assignment-sidebar"
        collapsible="icon"
        aria-label="Assignment navigation"
      >
        <SidebarHeader className="h-14 shrink-0 flex-row items-center gap-0 overflow-hidden border-b border-sidebar-border px-2">
          <SidebarTrigger
            className="h-11 w-12 shrink-0 active:translate-y-0!"
            aria-label={expanded ? 'Collapse navigation' : 'Expand navigation'}
            aria-expanded={expanded}
          />
          <Link
            to="/assignments"
            onClick={close}
            className="min-w-0 truncate text-sm font-medium group-data-[collapsible=icon]:hidden hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          >
            Opengrader
          </Link>
        </SidebarHeader>
        <SidebarContent className="overflow-x-hidden pt-2">
          {assignment && (
            <nav aria-label={assignment.title} className="w-full">
              <SidebarGroup className="px-2 py-0">
                <div
                  title={assignment.title}
                  className="flex h-6 shrink-0 items-center px-4 text-xs font-medium text-sidebar-foreground/70 select-none group-data-[collapsible=icon]:invisible"
                >
                  <span className="truncate">{assignment.title}</span>
                </div>
                <SidebarGroupContent className="mt-1">
                  <SidebarMenu>
                    {destinations.map(({ to, title, icon: Icon }) => {
                      const active = Boolean(matchRoute({ to, pending: true }));
                      return (
                        <SidebarMenuItem key={to}>
                          <SidebarMenuButton
                            className="min-h-11 px-4 text-sm transition-none group-data-[collapsible=icon]:h-11! group-data-[collapsible=icon]:w-12! group-data-[collapsible=icon]:px-4! focus-visible:ring-2 focus-visible:ring-inset"
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
        <SidebarFooter className="items-start gap-2 px-4 py-3">
          <AuthControl compact={!expanded} />
          <ModeToggle compact={!expanded} />
        </SidebarFooter>
        <SidebarResizeHandle />
      </Sidebar>
    </>
  );
}
