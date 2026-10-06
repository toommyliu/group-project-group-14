import { Dialog } from '@base-ui/react/dialog';
import { ChevronLeft, ChevronRight, XIcon } from 'lucide-react';
import { scanPageUrl, type ScanPage } from '@/api/scans';
import { Button } from './ui/button';
import { usePortalContainer } from '@/lib/portal-container';

export type PreviewPage = ScanPage & { label: string };

/** Full-size view of one scan page, with arrow-key paging through `pages`. */
export function ScanPageDialog({
  assignmentId,
  batchId,
  pages,
  index,
  onIndexChange,
}: {
  assignmentId: string;
  batchId: string;
  pages: PreviewPage[];
  index: number | null;
  onIndexChange: (index: number | null) => void;
}) {
  const page = index === null ? undefined : pages[index];
  const sideways = page?.rotation === 90 || page?.rotation === 270;

  function step(delta: number) {
    if (index === null) return;
    const next = index + delta;
    if (next >= 0 && next < pages.length) onIndexChange(next);
  }

  return (
    <Dialog.Root
      open={page !== undefined}
      onOpenChange={(open) => {
        if (!open) onIndexChange(null);
      }}
    >
      <Dialog.Portal container={usePortalContainer()}>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/80 transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Dialog.Popup
          className="fixed inset-0 z-50 flex flex-col text-white outline-none"
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') step(-1);
            else if (event.key === 'ArrowRight') step(1);
            else return;
            event.preventDefault();
          }}
        >
          <div className="flex min-h-14 items-center gap-3 bg-black/70 px-4">
            <Dialog.Title className="min-w-0 flex-1 truncate text-sm font-medium">
              {page?.label}
            </Dialog.Title>
            <span className="text-sm text-white/70 tabular-nums">
              {index === null ? '' : `${index + 1} of ${pages.length}`}
            </span>
            <Dialog.Close
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-white hover:bg-white/15 hover:text-white"
                />
              }
            >
              <XIcon aria-hidden="true" />
              <span className="sr-only">Close preview</span>
            </Dialog.Close>
          </div>
          <div className="relative flex min-h-0 flex-1 items-center justify-center p-4">
            {page && (
              <img
                key={page.number}
                src={scanPageUrl(assignmentId, batchId, page.number, 'large')}
                alt={page.label}
                className="bg-white object-contain shadow-lg"
                style={{
                  transform: `rotate(${page.rotation}deg)`,
                  maxWidth: sideways ? 'calc(100dvh - 7rem)' : '100%',
                  maxHeight: sideways ? 'calc(100vw - 8rem)' : '100%',
                }}
              />
            )}
            <Button
              variant="ghost"
              size="icon-lg"
              className="absolute inset-y-0 left-4 my-auto bg-black/50 text-white hover:bg-black/70 hover:text-white"
              disabled={!index}
              onClick={() => step(-1)}
            >
              <ChevronLeft aria-hidden="true" />
              <span className="sr-only">Previous page</span>
            </Button>
            <Button
              variant="ghost"
              size="icon-lg"
              className="absolute inset-y-0 right-4 my-auto bg-black/50 text-white hover:bg-black/70 hover:text-white"
              disabled={index === null || index >= pages.length - 1}
              onClick={() => step(1)}
            >
              <ChevronRight aria-hidden="true" />
              <span className="sr-only">Next page</span>
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
