import { ErrorAlert } from '@/components/error-alert';
import { lazy, Suspense, useId, useState } from 'react';
import { LoaderCircle, X } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { useAssignmentPdf } from '@/hooks/use-assignment-pdf';
import { PaneLoading } from './loading';
import { PdfActionButton } from './pdf-action-button';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

// Lazy load since its not needed on render.
const AssignmentPdfViewer = lazy(() => import('./assignment-pdf-viewer'));

export function AssignmentPreview({
  source,
  preview,
  current: sourceCurrent,
  blocked = false,
  savedAssignment,
  onClose,
}: {
  source: string;
  preview?: QAPreview;
  current: boolean;
  blocked?: boolean;
  savedAssignment?: { id: string; source: string };
  onClose: () => void;
}) {
  const [selectedVersion, setSelectedVersion] = useState('');
  const versions = preview?.document.versions?.length
    ? preview.document.versions
    : ['A'];
  const version = versions.includes(selectedVersion)
    ? selectedVersion
    : versions[0];
  const valid = !!preview && sourceCurrent;
  const savedPdfAssignmentId =
    savedAssignment?.source === source ? savedAssignment.id : undefined;
  const { pdf, url, update, busy, current, error } = useAssignmentPdf(
    source,
    version,
    savedPdfAssignmentId
  );
  const currentPdf = valid && current;
  const versionId = useId();
  const headingId = useId();
  return (
    <section
      className="@container flex min-h-0 min-w-0 flex-1 flex-col"
      aria-labelledby={headingId}
    >
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b pb-2">
        <h2 id={headingId} className="flex min-h-11 items-center font-medium">
          PDF preview
        </h2>
        <div className="ms-auto flex items-center gap-2">
          {versions.length > 1 && (
            <>
              <Label htmlFor={versionId} className="text-sm">
                Version
              </Label>
              <Select
                value={version}
                disabled={blocked || !valid}
                onValueChange={(value) => {
                  if (value) setSelectedVersion(value);
                }}
              >
                <SelectTrigger id={versionId} className="max-w-full min-w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end" alignItemWithTrigger={false}>
                  {versions.map((item) => (
                    <SelectItem key={item} value={item}>
                      {item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close side panel"
            title="Close side panel"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="custom-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pe-2 pt-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={!valid || blocked || busy}
              onClick={() => void update()}
            >
              {busy && (
                <LoaderCircle
                  className="motion-safe:animate-spin"
                  aria-hidden="true"
                />
              )}
              Update PDF
            </Button>
            <PdfActionButton
              url={currentPdf ? url : undefined}
              filename={`assignment-${version.replace(/[^a-zA-Z0-9_-]/g, '_')}.pdf`}
            />
          </div>
          <p
            role="status"
            className="ms-auto max-w-full min-w-0 text-end text-xs break-words text-muted-foreground @max-[24rem]:ms-0 @max-[24rem]:w-full @max-[24rem]:text-start"
          >
            {busy
              ? 'Generating…'
              : pdf && !currentPdf
                ? `Outdated · Version ${pdf.version}`
                : !valid
                  ? 'Valid QA required'
                  : !pdf
                    ? 'Not generated'
                    : 'Up to date'}
          </p>
        </div>
        {error && <ErrorAlert error={error} />}
        {!!preview?.warnings.length && (
          <p className="border-s-2 ps-3">
            Referenced images are not included in the generated PDF.
          </p>
        )}
        {pdf && url && (
          <Suspense fallback={<PaneLoading label="Loading PDF viewer…" />}>
            <AssignmentPdfViewer key={pdf.version} blob={pdf.blob} />
          </Suspense>
        )}
      </div>
    </section>
  );
}
