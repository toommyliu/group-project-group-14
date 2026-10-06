import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { X } from 'lucide-react';
import { cn } from 'cn';
import { ErrorAlert } from './error-alert';
import { PaneLoading } from './loading';
import {
  MIN_BOX,
  NAME,
  STUDENT_ID,
  clampBox,
  rows,
  type Box,
  type Draft,
  type Owner,
} from '@/lib/outline-draft';

GlobalWorkerOptions.workerSrc = workerUrl;

export type BoxSelection = { owner: Owner; index: number };

export type Interaction = 'view' | 'adjust' | 'draw';

type Placed = BoxSelection & { box: Box; label: string; identity: boolean };

type Gesture =
  | { kind: 'draw'; page: number; start: [number, number]; box: Box }
  | {
      kind: 'move' | 'resize';
      target: BoxSelection;
      origin: Box;
      start: [number, number];
      corner: Corner;
    };

type Corner = 'nw' | 'ne' | 'sw' | 'se';

const corners: Corner[] = ['nw', 'ne', 'sw', 'se'];

function placedBoxes(draft: Draft): Placed[] {
  const placed: Placed[] = [];
  for (const row of rows(draft)) {
    row.leaf?.boxes.forEach((box, index) =>
      placed.push({
        owner: row.key,
        index,
        box,
        label: `${row.label} · ${row.leaf?.points ?? 0} pt${row.leaf?.points === 1 ? '' : 's'}`,
        identity: false,
      })
    );
  }
  if (draft.name)
    placed.push({
      owner: NAME,
      index: 0,
      box: draft.name,
      label: 'Name',
      identity: true,
    });
  if (draft.studentId)
    placed.push({
      owner: STUDENT_ID,
      index: 0,
      box: draft.studentId,
      label: 'ID',
      identity: true,
    });
  return placed;
}

function resized(origin: Box, corner: Corner, dx: number, dy: number): Box {
  let x0 = origin.x;
  let y0 = origin.y;
  let x1 = origin.x + origin.width;
  let y1 = origin.y + origin.height;
  if (corner.includes('w')) x0 = Math.min(x1 - MIN_BOX, Math.max(0, x0 + dx));
  else x1 = Math.max(x0 + MIN_BOX, Math.min(1, x1 + dx));
  if (corner.includes('n')) y0 = Math.min(y1 - MIN_BOX, Math.max(0, y0 + dy));
  else y1 = Math.max(y0 + MIN_BOX, Math.min(1, y1 + dy));
  return { page: origin.page, x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function OutlineTemplate({
  url,
  draft,
  selected,
  activeRow,
  interaction,
  focus,
  onSelect,
  onDraw,
  onChange,
  onRemove,
  onLeave,
  overlay,
}: {
  url: string;
  draft: Draft;
  selected: BoxSelection | null;
  activeRow: string | null;
  interaction: Interaction;
  focus: { owner: Owner; nonce: number; index?: number } | null;
  onSelect: (selection: BoxSelection | null) => void;
  onDraw: (box: Box) => void;
  onChange: (target: BoxSelection, box: Box) => void;
  onRemove: (target: BoxSelection) => void;
  onLeave: (owner: Owner) => void;
  overlay?: {
    url: (page: number) => string;
    opacity: number;
    attempt: number;
    onError: () => void;
  } | null;
}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy>();
  const [ratios, setRatios] = useState<number[]>([]);
  const [error, setError] = useState('');
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const focused = useRef(0);

  useEffect(() => {
    let active = true;
    let loading: PDFDocumentLoadingTask | undefined;
    void (async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`status ${response.status}`);
      const task = getDocument({ data: await response.arrayBuffer() });
      loading = task;
      const loaded = await task.promise;
      const sizes = await Promise.all(
        Array.from({ length: loaded.numPages }, async (_, index) => {
          const page = await loaded.getPage(index + 1);
          const viewport = page.getViewport({ scale: 1 });
          return viewport.height / viewport.width;
        })
      );
      if (!active) return void task.destroy();
      setRatios(sizes);
      setPdf(loaded);
    })().catch(() => {
      if (active)
        setError(
          'Could not open the template PDF. Reload the page, or replace the template.'
        );
    });
    return () => {
      active = false;
      void loading?.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (!focus || focus.nonce === focused.current) return;
    const box = scroller.current?.querySelector<HTMLElement>(
      `[data-owner="${CSS.escape(focus.owner)}"]${focus.index === undefined ? '' : `[data-index="${focus.index}"]`}`
    );
    if (!box) return;
    focused.current = focus.nonce;
    box.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (focus.index !== undefined) box.focus({ preventScroll: true });
  }, [focus, pdf]);

  const placed = placedBoxes(draft);
  const readOnly = interaction === 'view';

  function point(event: PointerEvent, page: HTMLElement): [number, number] {
    const rect = page.getBoundingClientRect();
    return [
      (event.clientX - rect.left) / rect.width,
      (event.clientY - rect.top) / rect.height,
    ];
  }

  function pageElement(event: PointerEvent) {
    return (event.currentTarget as HTMLElement).closest<HTMLElement>(
      '[data-page]'
    )!;
  }

  function startDraw(event: PointerEvent<HTMLDivElement>, page: number) {
    if (
      interaction !== 'draw' ||
      event.button !== 0 ||
      event.target !== event.currentTarget
    )
      return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = point(event, event.currentTarget);
    onSelect(null);
    setGesture({
      kind: 'draw',
      page,
      start,
      box: { page, x: start[0], y: start[1], width: 0, height: 0 },
    });
  }

  function startEdit(
    event: PointerEvent<HTMLElement>,
    item: Placed,
    kind: 'move' | 'resize',
    corner: Corner = 'se'
  ) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget
      .closest<HTMLElement>('[data-owner]')
      ?.focus({ preventScroll: true });
    onSelect({ owner: item.owner, index: item.index });
    if (readOnly) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setGesture({
      kind,
      target: { owner: item.owner, index: item.index },
      origin: item.box,
      start: point(event, pageElement(event)),
      corner,
    });
  }

  function drawn(event: PointerEvent<HTMLElement>, start: [number, number]) {
    const [x, y] = point(event, pageElement(event));
    const cx = Math.min(1, Math.max(0, x));
    const cy = Math.min(1, Math.max(0, y));
    return {
      x: Math.min(start[0], cx),
      y: Math.min(start[1], cy),
      width: Math.abs(cx - start[0]),
      height: Math.abs(cy - start[1]),
    };
  }

  function track(event: PointerEvent<HTMLElement>) {
    if (!gesture) return;
    if (readOnly) {
      setGesture(null);
      return;
    }
    const [x, y] = point(event, pageElement(event));
    if (gesture.kind === 'draw') {
      setGesture({
        ...gesture,
        box: { page: gesture.page, ...drawn(event, gesture.start) },
      });
      return;
    }
    const dx = x - gesture.start[0];
    const dy = y - gesture.start[1];
    onChange(
      gesture.target,
      gesture.kind === 'move'
        ? clampBox({
            ...gesture.origin,
            x: gesture.origin.x + dx,
            y: gesture.origin.y + dy,
          })
        : resized(gesture.origin, gesture.corner, dx, dy)
    );
  }

  function finish(event: PointerEvent<HTMLElement>) {
    if (gesture?.kind === 'draw' && interaction === 'draw') {
      const box = { page: gesture.page, ...drawn(event, gesture.start) };
      if (box.width >= MIN_BOX * 2 && box.height >= MIN_BOX * 2) onDraw(box);
    }
    setGesture(null);
  }

  function nudge(event: KeyboardEvent, item: Placed) {
    const step = event.shiftKey ? 0.02 : 0.0025;
    const delta = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }[event.key];
    if (event.key === 'Delete' || event.key === 'Backspace') {
      if (readOnly) return;
      event.preventDefault();
      onRemove(item);
      return;
    }
    if (event.key === 'Escape') {
      onLeave(item.owner);
      return;
    }
    if (!delta || readOnly) return;
    event.preventDefault();
    const [dx, dy] = delta;
    onChange(
      item,
      event.altKey
        ? resized(item.box, 'se', dx, dy)
        : clampBox({ ...item.box, x: item.box.x + dx, y: item.box.y + dy })
    );
  }

  if (error) return <ErrorAlert className="m-4" error={error} />;
  if (!pdf) return <PaneLoading label="Opening template…" />;

  return (
    <div
      ref={scroller}
      className="custom-scrollbar h-full min-h-0 overflow-auto bg-muted px-4 py-4 sm:px-6"
      role="region"
      aria-label="Template pages"
    >
      <div className="mx-auto grid max-w-[54rem] gap-6">
        {ratios.map((ratio, index) => {
          const page = index + 1;
          return (
            <section
              key={page}
              aria-label={`Page ${page}`}
              className="grid gap-1.5"
            >
              <span className="text-xs text-muted-foreground tabular-nums">
                Page {page} of {ratios.length}
              </span>
              <div
                data-page={page}
                className="relative w-full bg-white shadow-sm outline outline-black/10 select-none"
                style={{ aspectRatio: `1 / ${ratio}` }}
              >
                <TemplateCanvas pdf={pdf} page={page} />
                {overlay && (
                  <img
                    key={`${overlay.url(page)}#${overlay.attempt}`}
                    src={overlay.url(page)}
                    alt=""
                    draggable={false}
                    decoding="async"
                    loading="lazy"
                    className="pointer-events-none absolute inset-0 size-full"
                    style={{ opacity: overlay.opacity }}
                    onError={(event) => {
                      event.currentTarget.hidden = true;
                      overlay.onError();
                    }}
                  />
                )}
                <div
                  className={cn(
                    'absolute inset-0 touch-none',
                    interaction === 'draw' && 'cursor-crosshair'
                  )}
                  onPointerDown={(event) => startDraw(event, page)}
                  onPointerMove={track}
                  onPointerUp={finish}
                  onPointerCancel={() => setGesture(null)}
                >
                  {placed
                    .filter((item) => item.box.page === page)
                    .map((item) => {
                      const isSelected =
                        selected?.owner === item.owner &&
                        selected.index === item.index;
                      const highlighted =
                        isSelected || activeRow === item.owner;
                      return (
                        <div
                          key={`${item.owner}-${item.index}`}
                          data-owner={item.owner}
                          data-index={item.index}
                          role="button"
                          tabIndex={0}
                          aria-pressed={isSelected}
                          aria-label={`${item.identity ? `${item.label} box` : `Question ${item.label} box`}${readOnly ? '' : '. Arrow keys move it; hold Option or Alt to resize; Delete removes it.'}`}
                          className={cn(
                            'absolute touch-none border-2 outline-none',
                            item.identity
                              ? 'border-amber-600 bg-amber-400/15'
                              : 'border-sky-700 bg-sky-500/10',
                            highlighted &&
                              (item.identity
                                ? 'border-amber-700 bg-amber-400/30'
                                : 'border-sky-800 bg-sky-500/25'),
                            isSelected && 'z-10 ring-2 ring-sky-400/70',
                            !readOnly && 'cursor-move',
                            'focus-visible:ring-2 focus-visible:ring-ring'
                          )}
                          style={{
                            left: `${item.box.x * 100}%`,
                            top: `${item.box.y * 100}%`,
                            width: `${item.box.width * 100}%`,
                            height: `${item.box.height * 100}%`,
                          }}
                          onPointerDown={(event) =>
                            startEdit(event, item, 'move')
                          }
                          onKeyDown={(event) => nudge(event, item)}
                          onFocus={() =>
                            onSelect({ owner: item.owner, index: item.index })
                          }
                        >
                          <span
                            className={cn(
                              'absolute -top-px left-[-2px] flex max-w-[calc(100%+4px)] -translate-y-full items-center gap-1 px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-white',
                              item.identity ? 'bg-amber-600' : 'bg-sky-700',
                              highlighted &&
                                (item.identity ? 'bg-amber-700' : 'bg-sky-800')
                            )}
                          >
                            <span className="truncate">{item.label}</span>
                            {!readOnly && (
                              <button
                                type="button"
                                tabIndex={-1}
                                aria-label={`Remove ${item.label} box`}
                                className="-me-1 grid size-4 place-items-center hover:bg-white/20"
                                onPointerDown={(event) =>
                                  event.stopPropagation()
                                }
                                onClick={() => onRemove(item)}
                              >
                                <X className="size-3" aria-hidden="true" />
                              </button>
                            )}
                          </span>
                          {!readOnly &&
                            isSelected &&
                            corners.map((corner) => (
                              <span
                                key={corner}
                                aria-hidden="true"
                                className={cn(
                                  'absolute size-3 border-2 border-white bg-sky-700',
                                  item.identity && 'bg-amber-700',
                                  corner === 'nw' &&
                                    '-top-1.5 -left-1.5 cursor-nwse-resize',
                                  corner === 'ne' &&
                                    '-top-1.5 -right-1.5 cursor-nesw-resize',
                                  corner === 'sw' &&
                                    '-bottom-1.5 -left-1.5 cursor-nesw-resize',
                                  corner === 'se' &&
                                    '-right-1.5 -bottom-1.5 cursor-nwse-resize'
                                )}
                                onPointerDown={(event) =>
                                  startEdit(event, item, 'resize', corner)
                                }
                              />
                            ))}
                        </div>
                      );
                    })}
                  {gesture?.kind === 'draw' && gesture.page === page && (
                    <div
                      aria-hidden="true"
                      className="pointer-events-none absolute border-2 border-dashed border-sky-700 bg-sky-500/10"
                      style={{
                        left: `${gesture.box.x * 100}%`,
                        top: `${gesture.box.y * 100}%`,
                        width: `${gesture.box.width * 100}%`,
                        height: `${gesture.box.height * 100}%`,
                      }}
                    />
                  )}
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function TemplateCanvas({
  pdf,
  page,
}: {
  pdf: PDFDocumentProxy;
  page: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const element = canvas.current?.parentElement;
    if (!element) return;
    let timer: number | undefined;
    const resize = new ResizeObserver(([entry]) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setWidth(entry.contentRect.width), 120);
    });
    const intersection = new IntersectionObserver(
      ([entry]) => entry.isIntersecting && setVisible(true),
      { rootMargin: '600px 0px' }
    );
    resize.observe(element);
    intersection.observe(element);
    setWidth(element.getBoundingClientRect().width);
    return () => {
      window.clearTimeout(timer);
      resize.disconnect();
      intersection.disconnect();
    };
  }, []);

  useEffect(() => {
    const element = canvas.current;
    if (!element || !visible || width <= 0) return;
    let active = true;
    let task: RenderTask | undefined;
    void (async () => {
      const source = await pdf.getPage(page);
      if (!active) return;
      const natural = source.getViewport({ scale: 1 });
      const density = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = source.getViewport({ scale: width / natural.width });
      const next = document.createElement('canvas');
      next.width = Math.ceil(viewport.width * density);
      next.height = Math.ceil(viewport.height * density);
      task = source.render({
        canvas: next,
        viewport,
        transform: [density, 0, 0, density, 0, 0],
      });
      await task.promise;
      if (!active) return;
      element.width = next.width;
      element.height = next.height;
      element.getContext('2d')?.drawImage(next, 0, 0);
    })().catch(() => undefined);
    return () => {
      active = false;
      task?.cancel();
    };
  }, [pdf, page, width, visible]);

  return (
    <canvas
      ref={canvas}
      aria-hidden="true"
      className="absolute inset-0 size-full"
    />
  );
}
