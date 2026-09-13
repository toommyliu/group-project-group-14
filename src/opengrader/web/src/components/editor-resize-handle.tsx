import { useEffect, useRef, type RefObject } from 'react';
import { GripHorizontal } from 'lucide-react';

/** Keep a resize drag moving when the handle reaches the viewport edge. */
export function EditorResizeHandle({
  editorRef,
  disabled,
}: {
  editorRef: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
}) {
  const stopDrag = useRef<(() => void) | null>(null);
  useEffect(() => () => stopDrag.current?.(), []);

  return (
    <button
      type="button"
      disabled={disabled}
      aria-label="Resize QA editor height"
      aria-controls="qaMarkdown"
      className="flex h-6 w-full cursor-ns-resize touch-none items-center justify-center text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-default disabled:opacity-50"
      onKeyDown={(event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        const editor = editorRef.current;
        if (!editor) return;
        event.preventDefault();
        const step = event.shiftKey ? 80 : 20;
        const minimum = Number.parseFloat(getComputedStyle(editor).minHeight);
        editor.style.height = `${Math.max(minimum, editor.getBoundingClientRect().height + (event.key === 'ArrowDown' ? step : -step))}px`;
      }}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        const editor = editorRef.current;
        if (!editor) return;
        event.preventDefault();
        stopDrag.current?.();
        const handle = event.currentTarget;
        const pointerId = event.pointerId;
        handle.setPointerCapture(pointerId);
        const startHeight = editor.getBoundingClientRect().height;
        const startY = event.clientY;
        const startScroll = window.scrollY;
        const minimum = Number.parseFloat(getComputedStyle(editor).minHeight);
        let pointerY = startY;
        let frame = 0;
        let previousTime = performance.now();
        const move = (moveEvent: PointerEvent) => {
          if (moveEvent.pointerId === pointerId) pointerY = moveEvent.clientY;
        };
        const tick = (time: number) => {
          const elapsed = Math.min(time - previousTime, 32);
          previousTime = time;
          const edge = 40;
          const speed =
            pointerY > window.innerHeight - edge
              ? Math.min(1, (pointerY - window.innerHeight + edge) / edge)
              : pointerY < edge
                ? -Math.min(1, (edge - pointerY) / edge)
                : 0;
          const scroll = speed * elapsed * 0.6;
          editor.style.height = `${Math.max(minimum, startHeight + pointerY - startY + window.scrollY - startScroll + scroll)}px`;
          if (scroll) window.scrollBy({ top: scroll, behavior: 'instant' });
          frame = requestAnimationFrame(tick);
        };
        const stop = () => {
          cancelAnimationFrame(frame);
          handle.removeEventListener('pointermove', move);
          handle.removeEventListener('pointerup', stop);
          handle.removeEventListener('pointercancel', stop);
          handle.removeEventListener('lostpointercapture', stop);
          window.removeEventListener('blur', stop);
          if (handle.hasPointerCapture(pointerId))
            handle.releasePointerCapture(pointerId);
          stopDrag.current = null;
        };
        stopDrag.current = stop;
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', stop);
        handle.addEventListener('pointercancel', stop);
        handle.addEventListener('lostpointercapture', stop);
        window.addEventListener('blur', stop);
        frame = requestAnimationFrame(tick);
      }}
    >
      <GripHorizontal className="size-4" aria-hidden="true" />
    </button>
  );
}
