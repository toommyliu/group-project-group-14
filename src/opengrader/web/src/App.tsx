import { useState, type CSSProperties, type PropsWithChildren } from 'react';
import { useRouterState } from '@tanstack/react-router';
import { AppHeader } from '@/components/app-header';
import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { AppSidebar } from '@/components/app-sidebar';
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { TooltipProvider } from '@/components/ui/tooltip';
import { usePreference } from '@/hooks/use-preference';

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
  const [sidebarExpanded, setSidebarExpanded] =
    usePreference('sidebarExpanded');
  const [savedSidebarWidth, saveSidebarWidth] = usePreference('sidebarWidth');
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const sidebarWidth = dragWidth ?? savedSidebarWidth;
  const { assignment } = useAssignmentNavigation();
  const overview = useRouterState({
    select: ({ location }) => assignment && location.pathname === '/assignment',
  });
  return (
    <TooltipProvider>
      <SidebarProvider
        open={sidebarExpanded}
        onOpenChange={setSidebarExpanded}
        style={
          {
            '--sidebar-width': `${sidebarWidth}px`,
          } as CSSProperties
        }
        className={`${overview ? 'h-svh min-h-0 overflow-hidden' : assignment ? '' : 'block'} text-sm leading-normal antialiased has-data-[resizing=true]:select-none has-data-[resizing=true]:[&_[data-slot=sidebar-container]]:transition-none has-data-[resizing=true]:[&_[data-slot=sidebar-gap]]:transition-none`}
      >
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:start-4 focus:top-4 focus:z-[100] focus:bg-background focus:p-3 focus:outline-2"
        >
          Skip to content
        </a>
        {assignment ? (
          <AppSidebar
            width={sidebarWidth}
            onWidthChange={setDragWidth}
            onResizeEnd={(width) => {
              saveSidebarWidth(width);
              setDragWidth(null);
            }}
          />
        ) : (
          <AppHeader />
        )}
        <SidebarInset
          className={
            overview
              ? 'h-full min-h-0 min-w-0 overflow-hidden'
              : assignment
                ? 'min-w-0'
                : 'min-h-[calc(100svh-4rem)] min-w-0'
          }
        >
          <div
            className={
              assignment
                ? `mx-auto w-full max-w-[90rem] px-4 pt-4 ${overview ? 'flex h-full min-h-0 flex-col overflow-hidden' : 'pb-16'} focus:outline-none sm:px-8 md:pt-8`
                : 'mx-auto w-full max-w-[90rem] px-4 pt-8 pb-16 focus:outline-none sm:px-8 lg:px-14'
            }
            id="content"
            tabIndex={-1}
          >
            {assignment && <MobileSidebarTrigger />}
            {children}
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}
