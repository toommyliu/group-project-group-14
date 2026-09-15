import {
  useCallback,
  useEffect,
  useRef,
  type RefObject,
  type SyntheticEvent,
} from 'react';
import type { OutlineEntry } from '@/lib/assignment-outline';
import { measureSourceLines } from '@/lib/textarea-navigation';

/** Cache wrapped positions so scrolling only reads numbers, not layout. */
export function useMarkdownOutline(
  editorRef: RefObject<HTMLTextAreaElement | null>,
  entries: OutlineEntry[],
  current: boolean,
  onActiveChange: (key: string | null) => void
) {
  const positions = useRef<{ key: string; top: number; offset: number }[]>([]);
  const updateFromScroll = useCallback(
    (editor: HTMLTextAreaElement) => {
      const atEnd =
        editor.scrollTop > 0 &&
        editor.scrollTop + editor.clientHeight >= editor.scrollHeight - 1;
      const position = atEnd
        ? positions.current.at(-1)
        : positions.current.findLast(
            (position) =>
              position.top <= editor.scrollTop + editor.clientHeight / 3
          );
      onActiveChange(position?.key ?? positions.current[0]?.key ?? null);
    },
    [onActiveChange]
  );
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !current) {
      positions.current = [];
      return;
    }
    function measure() {
      if (!editor) return;
      const byLine = new Map(
        entries.flatMap((entry) =>
          entry.line === null ? [] : [[entry.line, entry.key] as const]
        )
      );
      positions.current = measureSourceLines(
        editor,
        [...byLine.keys()].sort((a, b) => a - b)
      ).map((position) => ({ ...position, key: byLine.get(position.line)! }));
      updateFromScroll(editor);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(editor);
    return () => observer.disconnect();
  }, [editorRef, entries, current, updateFromScroll]);

  return {
    onSelect(event: SyntheticEvent<HTMLTextAreaElement>) {
      if (!current) return;
      const offset = event.currentTarget.selectionStart;
      const position = positions.current.findLast(
        (position) => position.offset <= offset
      );
      onActiveChange(position?.key ?? null);
    },
    onScroll(event: SyntheticEvent<HTMLTextAreaElement>) {
      if (!current) return;
      updateFromScroll(event.currentTarget);
    },
  };
}
