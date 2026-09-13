import { lazy, Suspense, useId, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { useAssignmentPdf } from '@/hooks/use-assignment-pdf';
import { QAPreviewView } from './qa-preview';
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
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';

// Lazy load since its not needed on render.
const AssignmentPdfViewer = lazy(() => import('./assignment-pdf-viewer'));

type Props = {
  preview?: QAPreview;
  blocked?: boolean;
} & (
  | { source: string; current: boolean; assignmentId?: never }
  | {
      assignmentId: string;
      source?: never;
      current?: never;
    }
);

export function AssignmentPreview(props: Props) {
  const { preview, blocked = false } = props;
  const [selectedVersion, setSelectedVersion] = useState('');
  const versions = preview?.document.versions?.length
    ? preview.document.versions
    : ['A'];
  const version = versions.includes(selectedVersion)
    ? selectedVersion
    : versions[0];
  const valid =
    !!preview && (props.assignmentId !== undefined || props.current === true);
  const { pdf, url, update, busy, current, error } = useAssignmentPdf(
    props.source ?? props.assignmentId,
    version,
    props.assignmentId
  );
  const currentPdf = valid && current;
  const versionId = useId();
  return (
    <section className="min-w-0 space-y-4" aria-label="Assignment preview">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">
          {preview?.document.title || 'Preview'}
        </h2>
        {versions.length > 1 && (
          <div className="flex items-center gap-2">
            <Label htmlFor={versionId}>Version</Label>
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
          </div>
        )}
      </div>
      <Tabs defaultValue="pdf">
        <TabsList aria-label="Preview view">
          <TabsTrigger value="pdf">PDF preview</TabsTrigger>
          <TabsTrigger value="guide">Grading guide</TabsTrigger>
        </TabsList>
        <TabsContent
          value="pdf"
          keepMounted
          className="space-y-4 data-hidden:hidden"
        >
          <div className="flex flex-wrap items-center gap-2">
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
            <p
              role="status"
              className="ms-auto text-end text-xs text-muted-foreground"
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
          {error && <p role="alert">{error}</p>}
          {!!preview?.warnings.length && (
            <p className="border-s-2 ps-3">
              Referenced images are not included in the generated PDF.
            </p>
          )}
          {pdf && url && (
            <Suspense fallback={<p role="status">Loading PDF viewer…</p>}>
              <AssignmentPdfViewer key={url} blob={pdf.blob} />
            </Suspense>
          )}
        </TabsContent>
        <TabsContent
          value="guide"
          className="max-h-[70svh] overflow-y-auto pe-2"
        >
          {valid && preview ? (
            <QAPreviewView preview={preview} versionOverride={version} />
          ) : (
            <p className="py-4 text-muted-foreground">
              The grading guide will update when the QA markdown is valid.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}
