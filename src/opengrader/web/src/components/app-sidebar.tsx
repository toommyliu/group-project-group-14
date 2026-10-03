import { workspaceBase } from '@/api/client';
import { useContext } from 'react';
import { WorkspacePages } from '@/lib/workspace-pages';
import { Link, useMatchRoute } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { BookOpen, ChartColumn, FileText, PenLine, Users } from 'lucide-react';
import { authQuery } from '@/api/auth';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { useFolderSession } from '@/hooks/use-folder-session';
import { ModeToggle } from './mode-toggle';
import { AuthControl } from './auth-control';
import { ScansIcon } from './scans-icon';
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
  SidebarSeparator,
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
      page.path === '/assignments/$assignmentId/grade' &&
      page.data.assignment.id === assignment?.id
  );
  const gradingSelection =
    gradingPage?.path === '/assignments/$assignmentId/grade'
      ? {
          submission: gradingPage.data.submission?.id,
          question: gradingPage.data.question?.id,
        }
      : {};
  const { setOpenMobile, isMobile, open, openMobile } = useSidebar();
  const browsing =
    (Boolean(workspaceBase) &&
      Boolean(matchRoute({ to: '/', pending: true }))) ||
    Boolean(matchRoute({ to: '/assignments', pending: true })) ||
    Boolean(matchRoute({ to: '/assignments/new', pending: true }));
  const auth = useQuery(authQuery);
  const managesPeople =
    Boolean(workspaceBase) && auth.data?.role === 'instructor';
  const onPeople = Boolean(matchRoute({ to: '/people', pending: true }));
  const expanded = isMobile ? openMobile : open;
  const courseLinks =
    Boolean(workspaceBase) && (!folderSession || managesPeople);
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
        icon: ScansIcon,
      },
      {
        to: '/assignments/$assignmentId/grade',
        title: 'Grade',
        icon: PenLine,
      },
      {
        to: '/assignments/$assignmentId/statistics',
        title: 'Statistics',
        icon: ChartColumn,
      },
    ] as const
  ).filter((destination) => !folderSession || destination.title !== 'Scans');
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
              className="-ms-2 min-w-0 truncate text-sm font-medium group-data-[collapsible=icon]:hidden hover:underline"
            >
              Opengrader
            </Link>
          ) : (
            <span className="-ms-2 min-w-0 truncate text-sm font-medium group-data-[collapsible=icon]:hidden">
              Opengrader
            </span>
          )}
        </SidebarHeader>
        <SidebarContent className="overflow-x-hidden pt-2">
          {courseLinks && (
            <nav aria-label="Workspace" className="w-full">
              <SidebarGroup className="px-2 py-0">
                <SidebarMenu>
                  {!folderSession && (
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        className={menuButtonClass}
                        tooltip="Assignments"
                        aria-label="Assignments"
                        isActive={browsing}
                        render={<Link to="/assignments" onClick={close} />}
                        aria-current={browsing ? 'page' : undefined}
                      >
                        <FileText aria-hidden="true" />
                        <span className="group-data-[collapsible=icon]:hidden">
                          Assignments
                        </span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )}
                  {managesPeople && (
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        className={menuButtonClass}
                        tooltip="People"
                        aria-label="People"
                        isActive={onPeople}
                        render={<Link to="/people" onClick={close} />}
                        aria-current={onPeople ? 'page' : undefined}
                      >
                        <Users aria-hidden="true" />
                        <span className="group-data-[collapsible=icon]:hidden">
                          People
                        </span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )}
                </SidebarMenu>
              </SidebarGroup>
            </nav>
          )}
          {assignment && (
            <nav aria-label={assignment.title} className="w-full">
              <SidebarGroup className="px-2 py-0">
                {courseLinks && (
                  <SidebarSeparator className="mx-4 my-2 group-data-[collapsible=icon]:mx-2" />
                )}
                <SidebarGroupContent>
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
                                  to === '/assignments/$assignmentId/grade'
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
