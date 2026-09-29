import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, FileText, FileUp, Upload, X } from 'lucide-react';
import type { scansRoute } from '@/router';
import { analyzeScan, scansQuery, uploadScan } from '@/api/scans';
import { PageHeading } from '@/components/page-heading';
import { ScanBatchCard } from '@/components/scan-batch';
import { Button } from '@/components/ui/button';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { errorMessage } from '@/lib/error-message';
import { ErrorAlert } from '@/components/error-alert';

type Transfer = {
  id: string;
  file: File;
  percent: number;
  status: 'waiting' | 'uploading' | 'failed';
  error: string;
};

function hasFiles(event: DragEvent) {
  return event.dataTransfer?.types.includes('Files') ?? false;
}

export default function ScansPage({
  data,
}: {
  data: typeof scansRoute.types.loaderData;
}) {
  const { assignment, versions } = data;
  const query = useQuery(scansQuery(assignment.id));
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const uploading = useRef(false);
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [refreshError, setRefreshError] = useState('');
  const [dragging, setDragging] = useState(false);
  const batches = query.data?.batches ?? [];
  const active = batches.filter((batch) => batch.status !== 'removed');
  const published = active.reduce(
    (total, batch) =>
      total +
      batch.groups.filter((group) => group.submission_id !== null).length,
    0
  );
  const reviewing = active.filter((batch) => batch.status === 'review').length;
  const failed = active.filter((batch) => batch.status === 'failed').length;

  useEffect(() => {
    void client.invalidateQueries({
      queryKey: ['assignments', assignment.id, 'submissions'],
    });
  }, [published, client, assignment.id]);

  const dropFiles = useEffectEvent((files: File[]) => void upload(files));

  // Accept PDFs dropped anywhere on the page.
  useEffect(() => {
    let depth = 0;
    const enter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth += 1;
      setDragging(true);
    };
    const over = (event: DragEvent) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const leave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      dropFiles(Array.from(event.dataTransfer?.files ?? []));
    };
    window.addEventListener('dragenter', enter);
    window.addEventListener('dragover', over);
    window.addEventListener('dragleave', leave);
    window.addEventListener('drop', drop);
    return () => {
      window.removeEventListener('dragenter', enter);
      window.removeEventListener('dragover', over);
      window.removeEventListener('dragleave', leave);
      window.removeEventListener('drop', drop);
    };
  }, []);

  async function upload(files: File[]) {
    if (uploading.current || !files.length) return;
    uploading.current = true;
    const additions: Transfer[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      percent: 0,
      status: 'waiting',
      error: '',
    }));
    setTransfers((previous) => [...previous, ...additions]);
    const update = (id: string, fields: Partial<Transfer>) =>
      setTransfers((previous) =>
        previous.map((item) => (item.id === id ? { ...item, ...fields } : item))
      );
    try {
      for (const item of additions) {
        if (!item.file.name.toLowerCase().endsWith('.pdf')) {
          update(item.id, { status: 'failed', error: 'Choose a PDF file.' });
          continue;
        }
        if (
          item.file.size > (query.data?.max_file_bytes ?? 250 * 1024 * 1024)
        ) {
          update(item.id, {
            status: 'failed',
            error: 'File is too large. Choose a PDF of 250 MB or less.',
          });
          continue;
        }
        update(item.id, { status: 'uploading' });
        try {
          const batch = await uploadScan(assignment.id, item.file, (percent) =>
            update(item.id, { percent })
          );
          if (batch.status === 'uploaded' && !batch.groups.length) {
            // Start splitting right away; the scan card shows any failure.
            await analyzeScan(assignment.id, batch, 'automatic', null).catch(
              () => undefined
            );
          }
          setTransfers((previous) =>
            previous.filter((transfer) => transfer.id !== item.id)
          );
        } catch (cause) {
          update(item.id, {
            status: 'failed',
            error: errorMessage(
              cause,
              'Could not upload this file. Try again.'
            ),
          });
        }
        try {
          await client.fetchQuery(scansQuery(assignment.id));
          setRefreshError('');
        } catch {
          setRefreshError(
            'Could not refresh scans. Reload the list to check completed uploads.'
          );
        }
      }
    } finally {
      uploading.current = false;
    }
  }

  const busy = transfers.some(
    (item) => item.status === 'uploading' || item.status === 'waiting'
  );
  const empty = !batches.length && !transfers.length;
  const summary = [
    `${active.length} ${active.length === 1 ? 'scan' : 'scans'}`,
    `${published} ${published === 1 ? 'submission' : 'submissions'} created`,
    reviewing > 0 && `${reviewing} to review`,
    failed > 0 && `${failed} need attention`,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      <PageHeading
        title="Scans"
        description={
          empty ? (
            assignment.title
          ) : (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>{summary}</span>
              {published > 0 && (
                <Link
                  to="/assignments/$assignmentId/grade"
                  params={{ assignmentId: assignment.id }}
                  className="inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline"
                >
                  Start grading
                  <ArrowRight className="size-3.5" aria-hidden="true" />
                </Link>
              )}
            </span>
          )
        }
        actions={
          !empty && (
            <Button disabled={busy} onClick={() => input.current?.click()}>
              <Upload aria-hidden="true" />
              {busy ? 'Uploading…' : 'Upload PDFs'}
            </Button>
          )
        }
      />
      <input
        ref={input}
        type="file"
        accept=".pdf,application/pdf"
        multiple
        className="hidden"
        aria-label="Upload PDF scans"
        onChange={(event) => {
          void upload(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      {(query.isError || refreshError) && (
        <ErrorAlert
          className="mb-4"
          title="Could not refresh scan status"
          error={refreshError || query.error}
        >
          <Button variant="outline" onClick={() => void query.refetch()}>
            Reload scans
          </Button>
        </ErrorAlert>
      )}
      {transfers.length > 0 && (
        <ul className="mb-3 grid gap-3" aria-live="polite">
          {transfers.map((item) => (
            <li key={item.id} className="border bg-card px-3 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <FileText
                  className="size-5 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate font-medium">
                  {item.file.name}
                </span>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {item.status === 'uploading'
                    ? item.percent === 100
                      ? 'Checking PDF…'
                      : `Uploading ${item.percent}%`
                    : item.status === 'waiting'
                      ? 'Waiting'
                      : 'Upload failed'}
                </span>
                {item.status === 'failed' && (
                  <>
                    <Button
                      size="lg"
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        setTransfers((previous) =>
                          previous.filter((transfer) => transfer.id !== item.id)
                        );
                        void upload([item.file]);
                      }}
                    >
                      Retry
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="ghost"
                      aria-label={`Dismiss ${item.file.name}`}
                      onClick={() =>
                        setTransfers((previous) =>
                          previous.filter((transfer) => transfer.id !== item.id)
                        )
                      }
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </>
                )}
              </div>
              {item.error && <ErrorAlert className="mt-2" error={item.error} />}
              {item.status === 'uploading' && (
                <div
                  className="mt-3 h-1.5 overflow-hidden bg-muted"
                  role="progressbar"
                  aria-label={`Uploading ${item.file.name}`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={item.percent}
                >
                  <div
                    className="h-full bg-primary transition-[width] motion-reduce:transition-none"
                    style={{ width: `${item.percent}%` }}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {empty ? (
        <Empty className="border py-16">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileUp />
            </EmptyMedia>
            <EmptyTitle>Upload scanned exams</EmptyTitle>
            <EmptyDescription>
              Drop PDFs anywhere on this page. One PDF can hold a whole class:
              Opengrader finds where each student's work starts, and you check
              the split before any submissions are created.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => input.current?.click()}>
              <Upload aria-hidden="true" />
              Select PDF files
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <div className="grid gap-3">
          {batches.map((batch) => (
            <ScanBatchCard
              key={batch.id}
              assignmentId={assignment.id}
              batch={batch}
              versions={versions}
            />
          ))}
        </div>
      )}
      {dragging && (
        <div
          className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-6"
          aria-hidden="true"
        >
          <div className="flex flex-col items-center gap-3 border-2 border-dashed border-foreground/40 px-16 py-12 text-center">
            <FileUp className="size-8" />
            <p className="text-lg font-medium">Drop PDFs to upload</p>
          </div>
        </div>
      )}
    </>
  );
}
