import { useRef, useState } from 'react';
import { useSidebar } from './ui/sidebar';
import { sidebarWidthBounds } from '@/lib/preferences';

const {
  min: SIDEBAR_MIN_WIDTH,
  max: SIDEBAR_MAX_WIDTH,
  default: SIDEBAR_DEFAULT_WIDTH,
} = sidebarWidthBounds;
const SIDEBAR_ICON_WIDTH = 64;

export function SidebarResizeHandle({
  width,
  onWidthChange,
  onResizeEnd,
}: {
  width: number;
  onWidthChange: (width: number | null) => void;
  onResizeEnd: (width: number) => void;
}) {
  const { open, setOpen, isMobile } = useSidebar();
  const drag = useRef<{ x: number; width: number; open: boolean } | null>(null);
  const [resizing, setResizing] = useState(false);
  if (isMobile) return null;

  function resize(candidate: number, persist = false) {
    if (candidate < (SIDEBAR_MIN_WIDTH + SIDEBAR_ICON_WIDTH) / 2) {
      if (open) setOpen(false);
      return;
    }
    const next =
      Math.abs(candidate - SIDEBAR_DEFAULT_WIDTH) <= 10
        ? SIDEBAR_DEFAULT_WIDTH
        : Math.round(
            Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, candidate))
          );
    if (!open) setOpen(true);
    if (persist) onResizeEnd(next);
    else onWidthChange(next);
  }

  function finish(cancel = false) {
    const start = drag.current;
    if (!start) return;
    drag.current = null;
    setResizing(false);
    if (cancel) {
      onWidthChange(null);
      setOpen(start.open);
    } else {
      onResizeEnd(width);
    }
  }

  return (
    <div
      role="separator"
      tabIndex={0}
      aria-label="Resize assignment navigation"
      aria-description="Use arrow keys to resize, Enter to collapse or expand, and Home to reset."
      aria-orientation="vertical"
      aria-controls="assignment-sidebar"
      aria-valuemin={SIDEBAR_ICON_WIDTH}
      aria-valuemax={SIDEBAR_MAX_WIDTH}
      aria-valuenow={open ? width : SIDEBAR_ICON_WIDTH}
      aria-valuetext={open ? `${width} pixels` : 'Collapsed to icons'}
      title="Drag to resize. Double-click to reset."
      data-resizing={resizing}
      className="absolute inset-y-0 -right-3 z-30 hidden w-6 cursor-col-resize touch-none items-center justify-center outline-none select-none after:h-full after:w-px hover:after:bg-foreground/40 focus-visible:after:w-0.5 focus-visible:after:bg-ring data-[resizing=true]:after:bg-ring md:flex"
      onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { x: event.clientX, width, open };
        setResizing(true);
      }}
      onPointerMove={(event) => {
        if (
          !drag.current ||
          !event.currentTarget.hasPointerCapture(event.pointerId)
        )
          return;
        const start = drag.current;
        resize(
          (start.open ? start.width : SIDEBAR_ICON_WIDTH) +
            event.clientX -
            start.x
        );
      }}
      onPointerUp={() => finish()}
      onPointerCancel={() => finish(true)}
      onLostPointerCapture={() => finish()}
      onDoubleClick={() => resize(SIDEBAR_DEFAULT_WIDTH, true)}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 48 : 16;
        switch (event.key) {
          case 'ArrowLeft':
            resize(
              open && width > SIDEBAR_MIN_WIDTH
                ? width - step
                : SIDEBAR_ICON_WIDTH,
              true
            );
            break;
          case 'ArrowRight':
            resize(open ? width + step : width, true);
            break;
          case 'Home':
            resize(SIDEBAR_DEFAULT_WIDTH, true);
            break;
          case 'End':
            resize(SIDEBAR_MAX_WIDTH, true);
            break;
          case 'Enter':
            setOpen(!open);
            break;
          case 'Escape':
            finish(true);
            break;
          default:
            return;
        }
        event.preventDefault();
      }}
    />
  );
}
