import { useContext } from 'react';
import { WorkspacePages } from '@/lib/workspace-pages';
import { Link } from '@tanstack/react-router';
import { BookOpen, Files, ScanLine, TerminalSquare } from 'lucide-react';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { ModeToggle } from './mode-toggle';
import { SidebarResizeHandle } from './sidebar-resize-handle';
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
  useSidebar,
  SidebarTrigger,
} from './ui/sidebar';

export function AppSidebar({
  width,
  onWidthChange,
  onResizeEnd,
}: {
  width: number;
  onWidthChange: (width: number | null) => void;
  onResizeEnd: (width: number) => void;
}) {
  const { assignment, pathname } = useAssignmentNavigation();
  const pages = useContext(WorkspacePages);
  const gradingPage = pages.find(
    (page) =>
      page.path === '/submissions' && page.data.assignment.id === assignment?.id
  );
  const gradingSelection =
    gradingPage?.path === '/submissions'
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
      to: '/assignment',
      prefix: '/assignment',
      title: 'Overview',
      icon: BookOpen,
    },
    {
      to: '/scans',
      prefix: '/scans',
      title: 'Scans',
      icon: ScanLine,
    },
    {
      to: '/submissions',
      prefix: '/submissions',
      title: 'Submissions',
      icon: Files,
    },
    // {
    //   to: '/terminals',
    //   prefix: '/terminals',
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
            to="/"
            onClick={close}
            aria-label="Opengrader home"
            aria-current={pathname === '/' ? 'page' : undefined}
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
                    {destinations.map(({ to, prefix, title, icon: Icon }) => {
                      const active =
                        pathname === prefix ||
                        (title === 'Overview' &&
                          pathname === '/edit-assignment');
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
                                search={{
                                  ...(to === '/submissions'
                                    ? gradingSelection
                                    : {}),
                                  assignment: assignment.id,
                                }}
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
        <SidebarFooter className="items-start px-4 py-3">
          <ModeToggle compact={!expanded} />
        </SidebarFooter>
        <SidebarResizeHandle
          width={width}
          onWidthChange={onWidthChange}
          onResizeEnd={onResizeEnd}
        />
      </Sidebar>
    </>
  );
}
