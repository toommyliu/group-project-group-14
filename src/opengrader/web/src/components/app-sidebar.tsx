import { Link } from '@tanstack/react-router';
import { BookOpen, Files, ScanLine } from 'lucide-react';
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
  const { setOpenMobile, isMobile, open, openMobile } = useSidebar();
  const expanded = isMobile ? openMobile : open;
  const close = () => setOpenMobile(false);
  const destinations = [
    {
      to: '/assignment/$assignmentId',
      prefix: '/assignment/',
      title: 'Overview',
      icon: BookOpen,
    },
    {
      to: '/scans/$assignmentId',
      prefix: '/scans/',
      title: 'Scans',
      icon: ScanLine,
    },
    {
      to: '/grade/$assignmentId',
      prefix: '/grade/',
      title: 'Submissions',
      icon: Files,
    },
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
                        pathname.startsWith(prefix) ||
                        (title === 'Overview' &&
                          pathname.startsWith('/edit-assignment/'));
                      return (
                        <SidebarMenuItem key={to}>
                          <SidebarMenuButton
                            className="min-h-11 px-4 text-sm transition-none group-data-[collapsible=icon]:h-11! group-data-[collapsible=icon]:w-12! group-data-[collapsible=icon]:px-4!"
                            tooltip={title}
                            aria-label={title}
                            isActive={active}
                            render={
                              <Link
                                to={to}
                                params={{ assignmentId: assignment.id }}
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
