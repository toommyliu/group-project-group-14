import { usePreference } from '@/hooks/use-preference';
import { useState } from 'react';
import { useGradingHotkeys as useHotkeys } from '@/hooks/use-grading-hotkeys';
import { ExternalLink, RotateCw, ZoomIn, ZoomOut } from 'lucide-react';
import { Button } from './ui/button';
import { Skeleton } from './ui/skeleton';

export function AnswerCrop({
  url,
  width,
  height,
  submissionId,
  questionId,
}: {
  url: string;
  width: number | null;
  height: number | null;
  submissionId: number;
  questionId: string;
}) {
  const [state, setState] = useState<'loading' | 'loaded' | 'failed'>(
    'loading'
  );
  const [shortcutsEnabled] = usePreference('gradingShortcuts');
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const rotate = () => setRotation((value) => (value + 90) % 360);
  const zoomIn = () => setZoom((value) => Math.min(3, value + 0.25));
  const zoomOut = () => setZoom((value) => Math.max(0.5, value - 0.25));
  useHotkeys(
    [
      { hotkey: { key: '=', shift: true }, callback: zoomIn },
      { hotkey: '=', callback: zoomIn },
      { hotkey: '-', callback: zoomOut },
      {
        hotkey: '0',
        callback: () => {
          setZoom(1);
          setRotation(0);
        },
      },
      { hotkey: 'R', callback: rotate },
    ],
    { enabled: shortcutsEnabled && state === 'loaded', ignoreInputs: true }
  );
  const ratio = width && height ? width / height : 4 / 3;
  const sideways = rotation % 180 !== 0;
  return (
    <div className="overflow-hidden rounded-lg border bg-muted/30">
      <div className="flex flex-wrap items-center gap-2 border-b p-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom out"
          title="Zoom out (−)"
          disabled={zoom <= 0.5}
          onClick={zoomOut}
        >
          <ZoomOut />
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setZoom(1);
            setRotation(0);
          }}
          title="Fit image (0)"
        >
          {Math.round(zoom * 100)}% · Fit
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom in"
          title="Zoom in (+)"
          disabled={zoom >= 3}
          onClick={zoomIn}
        >
          <ZoomIn />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Rotate image"
          title="Rotate image (R)"
          onClick={rotate}
        >
          <RotateCw />
        </Button>
        <a
          className="ms-auto flex min-h-10 items-center gap-2 px-2 text-sm hover:underline hover:underline-offset-4"
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          Open image <ExternalLink size={14} aria-hidden="true" />
        </a>
      </div>
      <div
        className="max-h-[65svh] overflow-auto overscroll-contain p-4"
        tabIndex={0}
        aria-label="Answer image. Scroll to inspect zoomed work."
        aria-busy={state === 'loading'}
      >
        {state === 'failed' ? (
          <Button variant="outline" onClick={() => setState('loading')}>
            Image unavailable. Retry
          </Button>
        ) : (
          <div
            style={{
              width: `${zoom * 100}%`,
              aspectRatio: sideways ? 1 / ratio : ratio,
            }}
            className="relative mx-auto"
          >
            {state === 'loading' && <Skeleton className="absolute inset-0" />}
            <img
              src={url}
              alt={`Scanned answer for submission ${submissionId}, question ${questionId}`}
              onLoad={() => setState('loaded')}
              onError={() => setState('failed')}
              className="absolute top-1/2 left-1/2 max-w-none outline outline-black/10 dark:outline-white/10"
              style={{
                width: sideways ? `${ratio * 100}%` : '100%',
                transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
              }}
            />
          </div>
        )}
      </div>
      <p role="status" className="sr-only">
        {state === 'failed'
          ? 'Image unavailable'
          : `Zoom ${Math.round(zoom * 100)} percent, rotation ${rotation} degrees`}
      </p>
    </div>
  );
}
