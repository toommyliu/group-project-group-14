import { LoadingIndicator } from './loading';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
  type PDFDocumentLoadingTask,
  type RenderTask,
} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from './ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

GlobalWorkerOptions.workerSrc = workerUrl;

const resizeDebounceMs = 120;

const zoomOptions = [
  { value: 0, label: 'Fit width' },
  ...[0.5, 0.75, 1, 1.25, 1.5, 2].map((value) => ({
    value,
    label: `${value * 100}%`,
  })),
];

export default function AssignmentPdfViewer({ blob }: { blob: Blob }) {
  const [document, setDocument] = useState<PDFDocumentProxy>();
  const [selectedPage, setPageNumber] = useState(1);
  const pageNumber = Math.min(selectedPage, document?.numPages ?? selectedPage);
  const [zoom, setZoom] = useState(0);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState('');
  const [rendering, setRendering] = useState(true);
  const [renderedPage, setRenderedPage] = useState<number>();
  const [text, setText] = useState('');
  const canvas = useRef<HTMLCanvasElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const scrollPositions = useRef(
    new Map<number, { left: number; top: number }>()
  );

  function goToPage(nextPage: number) {
    const element = container.current;
    if (element && renderedPage !== undefined) {
      scrollPositions.current.set(renderedPage, {
        left: element.scrollLeft,
        top: element.scrollTop,
      });
    }
    setPageNumber(nextPage);
  }

  useEffect(() => {
    let active = true;
    let task: PDFDocumentLoadingTask | undefined;
    void blob
      .arrayBuffer()
      .then(async (data) => {
        if (!active) return;
        task = getDocument({ data });
        const loaded = await task.promise;
        if (active) setDocument(loaded);
      })
      .catch(() => {
        if (active)
          setError(
            'Could not open this PDF. Download it to view it in a PDF reader.'
          );
      });
    return () => {
      active = false;
      void task?.destroy();
    };
  }, [blob]);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let measured = false;
    let timer: number | undefined;
    const observer = new ResizeObserver(([entry]) => {
      const nextWidth = entry.contentRect.width;
      if (!measured) {
        measured = true;
        setWidth(nextWidth);
        return;
      }
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        setWidth((current) => (current === nextWidth ? current : nextWidth));
      }, resizeDebounceMs);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  useEffect(
    () => () => {
      const element = canvas.current;
      if (!element) return;
      element.width = 0;
      element.height = 0;
    },
    []
  );

  useLayoutEffect(() => {
    if (renderedPage === undefined) return;
    const element = container.current;
    if (!element) return;
    const position = scrollPositions.current.get(renderedPage);
    element.scrollLeft = position?.left ?? 0;
    element.scrollTop = position?.top ?? 0;
  }, [renderedPage]);

  useEffect(() => {
    if (
      !document ||
      document.loadingTask.destroyed ||
      !canvas.current ||
      width <= 0
    )
      return;
    let active = true;
    let task: RenderTask | undefined;
    const element = canvas.current;
    const nextCanvas = window.document.createElement('canvas');
    setRendering(true);
    setError('');
    setText('');
    void (async () => {
      const page = await document.getPage(pageNumber);
      if (!active) return;
      const natural = page.getViewport({ scale: 1 });
      const scale = zoom || Math.max(0.1, (width - 24) / natural.width);
      const viewport = page.getViewport({ scale });
      const density = Math.min(window.devicePixelRatio || 1, 2);
      nextCanvas.width = Math.ceil(viewport.width * density);
      nextCanvas.height = Math.ceil(viewport.height * density);
      task = page.render({
        canvas: nextCanvas,
        viewport,
        transform: [density, 0, 0, density, 0, 0],
      });
      await task.promise;
      if (!active) return;
      element.width = nextCanvas.width;
      element.height = nextCanvas.height;
      element.style.width = `${viewport.width}px`;
      element.style.height = `${viewport.height}px`;
      const context = element.getContext('2d');
      if (!context) throw new Error('Could not create PDF canvas context');
      context.drawImage(nextCanvas, 0, 0);
      setRenderedPage(pageNumber);
      const content = await page.getTextContent();
      if (active) {
        setText(
          content.items
            .flatMap((item) => ('str' in item ? [item.str] : []))
            .join(' ')
        );
        setRendering(false);
      }
    })().catch(() => {
      if (active) {
        setError(
          'Could not display this page. Download the PDF to view it in a PDF reader.'
        );
        setRendering(false);
      }
    });
    return () => {
      active = false;
      task?.cancel();
    };
  }, [document, pageNumber, zoom, width]);

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex h-[36rem] max-h-[70svh] min-h-48 flex-col overflow-hidden border bg-muted">
        <div
          role="group"
          aria-label="PDF controls"
          className="z-10 flex shrink-0 flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b bg-background px-2 py-1"
        >
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              aria-label="Previous page"
              title="Previous page"
              className="size-8 min-h-8 p-0"
              disabled={!document || pageNumber === 1}
              onClick={() => goToPage(pageNumber - 1)}
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <span className="whitespace-nowrap tabular-nums">
              <span className="sr-only">Page </span>
              {pageNumber}
              {document ? ` / ${document.numPages}` : ''}
            </span>
            <Button
              type="button"
              variant="ghost"
              aria-label="Next page"
              title="Next page"
              className="size-8 min-h-8 p-0"
              disabled={!document || pageNumber === document.numPages}
              onClick={() => goToPage(pageNumber + 1)}
            >
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Select
              items={zoomOptions}
              value={zoom}
              onValueChange={(value) => {
                if (value !== null) setZoom(value);
              }}
            >
              <SelectTrigger aria-label="Zoom" className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false}>
                {zoomOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <LoadingIndicator
              loading={rendering && !error}
              label={!document ? 'Opening PDF…' : 'Rendering page…'}
            />
          </div>
        </div>
        {error && (
          <p role="alert" className="px-3 py-2 text-sm">
            {error}
          </p>
        )}
        <div
          ref={container}
          className="custom-scrollbar min-h-0 flex-1 overflow-auto"
          tabIndex={0}
          role="region"
          aria-label="Assignment PDF page"
          aria-busy={rendering && !error}
        >
          <canvas
            ref={canvas}
            role="img"
            aria-label={`Assignment PDF page ${pageNumber}`}
            className={`mx-auto my-3 bg-white outline outline-black/10 ${(renderedPage !== pageNumber && rendering) || error ? 'invisible' : ''}`}
          />
        </div>
      </div>
      <details hidden={!text}>
        <summary className="min-h-11 cursor-pointer py-3">Page text</summary>
        <p className="break-words whitespace-pre-wrap">{text}</p>
      </details>
    </div>
  );
}
