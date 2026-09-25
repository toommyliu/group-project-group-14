import type { CSSProperties, PropsWithChildren } from 'react';
import { useMatchRoute } from '@tanstack/react-router';
import { cn } from 'cn';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { useFolderSession } from '@/hooks/use-folder-session';
import { AppSidebar } from '@/components/app-sidebar';
import { FolderSessionBanner } from '@/components/folder-session-banner';
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
    <div className="mb-6 flex min-h-11 items-center md:hidden">
      <SidebarTrigger
        className="h-11 w-12 bg-sidebar active:translate-y-0!"
        aria-label="Open navigation"
        aria-expanded={openMobile}
      />
    </div>
  );
}

export function App({ children }: PropsWithChildren) {
  const sidebarExpanded = usePreferences((state) => state.sidebarExpanded);
  const sidebarWidth = useSidebarWidth();
  const { assignment, pathname } = useAssignmentNavigation();
  const folderSession = useFolderSession();
  const matchRoute = useMatchRoute();
  const overview =
    assignment &&
    Boolean(matchRoute({ to: '/assignments/$assignmentId', pending: true }));
  const home = !assignment && pathname === '/';
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
          overview ? 'h-svh min-h-0 overflow-hidden' : home ? 'block' : '',
          'text-sm leading-normal antialiased has-data-[resizing=true]:select-none has-data-[resizing=true]:[&_[data-slot=sidebar-container]]:transition-none has-data-[resizing=true]:[&_[data-slot=sidebar-gap]]:transition-none'
        )}
      >
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:start-4 focus:top-4 focus:z-[100] focus:bg-background focus:p-3 focus:outline-2"
        >
          Skip to content
        </a>
        {!home && <AppSidebar />}
        <SidebarInset
          className={
            overview
              ? 'h-full min-h-0 min-w-0 overflow-hidden'
              : home
                ? 'min-h-svh min-w-0'
                : 'min-w-0'
          }
        >
          <div
            className={
              !home
                ? `mx-auto w-full max-w-[90rem] px-4 pt-4 ${overview ? 'flex h-full min-h-0 flex-col overflow-hidden' : 'pb-16'} focus:outline-none sm:px-8 md:pt-8`
                : 'mx-auto w-full max-w-[90rem] px-4 pt-8 pb-16 focus:outline-none sm:px-8 lg:px-14'
            }
            id="content"
            tabIndex={-1}
          >
            {!home && <MobileSidebarTrigger />}
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
