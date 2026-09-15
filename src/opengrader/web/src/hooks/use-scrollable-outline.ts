import { useEffect, type RefObject } from 'react';
import type { OutlineEntry } from '@/lib/assignment-outline';

/** Follow the question nearest the top of a scrollable document. */
export function useScrollableOutline(
  containerRef: RefObject<HTMLElement | null>,
  entries: OutlineEntry[],
  targetId: (entry: OutlineEntry) => string,
  paused: boolean,
  onActiveChange: (key: string) => void
) {
  useEffect(() => {
    const currentContainer = containerRef.current;
    if (!currentContainer || paused) return;
    const container: HTMLElement = currentContainer;
    const targets = entries.flatMap((entry) => {
      const element = document.getElementById(targetId(entry));
      return element ? [{ key: entry.key, element }] : [];
    });
    if (!targets.length) return;
    let frame = 0;
    function update() {
      frame = 0;
      const atEnd =
        container.scrollTop > 0 &&
        container.scrollTop + container.clientHeight >=
          container.scrollHeight - 1;
      if (atEnd) {
        onActiveChange(targets.at(-1)!.key);
        return;
      }
      const bounds = container.getBoundingClientRect();
      const anchor = bounds.top + Math.min(160, container.clientHeight / 3);
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
    container.addEventListener('scroll', schedule, { passive: true });
    const observer = new ResizeObserver(schedule);
    observer.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      container.removeEventListener('scroll', schedule);
      observer.disconnect();
    };
  }, [containerRef, entries, targetId, paused, onActiveChange]);
}
