import { useEffect, type RefObject } from 'react';
import type { OutlineEntry } from '@/lib/assignment-outline';

export function useOutlineTracking({
  container: containerRef,
  entries,
  targetId,
  paused,
  onActiveChange,
}: {
  container?: RefObject<HTMLElement | null>;
  entries: OutlineEntry[];
  targetId: (entry: OutlineEntry) => string;
  paused: boolean;
  onActiveChange: (key: string) => void;
}) {
  useEffect(() => {
    const container = containerRef?.current ?? null;
    if (paused || (containerRef && !container)) return;
    const targets = entries.flatMap((entry) => {
      const element = document.getElementById(targetId(entry));
      return element ? [{ key: entry.key, element }] : [];
    });
    if (!targets.length) return;
    let frame = 0;
    function update() {
      frame = 0;
      if (
        container &&
        container.scrollTop > 0 &&
        container.scrollTop + container.clientHeight >=
          container.scrollHeight - 1
      ) {
        onActiveChange(targets.at(-1)!.key);
        return;
      }
      const anchor = container
        ? container.getBoundingClientRect().top +
          Math.min(160, container.clientHeight / 3)
        : Math.min(160, window.innerHeight / 3);
      let low = 0;
      let high = targets.length;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (targets[middle].element.getBoundingClientRect().top <= anchor)
          low = middle + 1;
        else high = middle;
      }
      onActiveChange(targets[Math.max(0, low - 1)].key);
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }
    schedule();
    const scroller = container ?? window;
    scroller.addEventListener('scroll', schedule, { passive: true });
    const observer = new ResizeObserver(schedule);
    if (container) observer.observe(container);
    else window.addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(frame);
      scroller.removeEventListener('scroll', schedule);
      observer.disconnect();
      window.removeEventListener('resize', schedule);
    };
  }, [containerRef, entries, targetId, paused, onActiveChange]);
}
