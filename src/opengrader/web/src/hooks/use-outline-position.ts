import { useEffect } from 'react';
import type { OutlineEntry } from '@/lib/assignment-outline';

/** Follow the visible question without measuring every card on each scroll. */
export function useOutlinePosition(
  entries: OutlineEntry[],
  version: string,
  paused: boolean,
  onActiveChange: (key: string) => void
) {
  useEffect(() => {
    if (paused) return;
    const targets = entries
      .filter((entry) => entry.version === version)
      .flatMap((entry) => {
        const element = document.getElementById(
          `visual-question-${entry.line}`
        );
        return element ? [{ key: entry.key, element }] : [];
      });
    if (!targets.length) return;
    let frame = 0;
    function update() {
      frame = 0;
      const anchor = Math.min(160, window.innerHeight / 3);
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
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
    };
  }, [entries, version, paused, onActiveChange]);
}
