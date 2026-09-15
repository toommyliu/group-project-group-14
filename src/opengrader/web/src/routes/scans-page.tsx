import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload } from 'lucide-react';
import type { scansRoute } from '@/router';
import { scansQuery, uploadScan } from '@/api/scans';
import { PageHeading } from '@/components/page-heading';
import { ScanBatchCard } from '@/components/scan-batch';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/error-message';
import { Alert, AlertDescription } from '@/components/ui/alert';

type Transfer = {
  id: string;
  file: File;
  percent: number;
  status: 'waiting' | 'uploading' | 'done' | 'failed';
  error: string;
};

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
  const [dragging, setDragging] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const batches = query.data?.batches ?? [];
  const published = batches.reduce(
    (total, batch) =>
      total +
      batch.groups.filter((group) => group.submission_id !== null).length,
    0
  );

  useEffect(() => {
    void client.invalidateQueries({
      queryKey: ['assignments', assignment.id, 'submissions'],
    });
  }, [published, client, assignment.id]);

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
    setTransfers((previous) => [
      ...previous.filter((item) => item.status !== 'done'),
      ...additions,
    ]);
    const update = (id: string, fields: Partial<Transfer>) =>
      setTransfers((previous) =>
        previous.map((item) => (item.id === id ? { ...item, ...fields } : item))
      );
    try {
      for (const item of additions) {
        if (!item.file.name.toLowerCase().endsWith('.pdf')) {
          update(item.id, {
            status: 'failed',
            error: 'Choose a PDF file.',
          });
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
          await uploadScan(assignment.id, item.file, (percent) =>
            update(item.id, { percent })
          );
          update(item.id, { status: 'done', percent: 100 });
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

  const active = transfers.some(
    (item) => item.status === 'uploading' || item.status === 'waiting'
  );
  return (
    <>
      <div className="@container">
        <div className="mb-8 flex flex-col gap-6 @min-[40rem]:flex-row @min-[40rem]:items-start">
          <PageHeading
            className="mb-0 min-w-0 flex-1"
            title="Manage scans"
            description={assignment.title}
          />
          <div
            className={`w-full shrink-0 rounded-lg border border-dashed p-4 @min-[40rem]:w-64 ${dragging ? 'bg-muted' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              void upload(Array.from(event.dataTransfer.files));
            }}
          >
            <div className="space-y-1">
              <h2 className="text-sm font-medium">Upload scans</h2>
              <p className="text-sm text-muted-foreground">
                Drop PDFs here or select files.
              </p>
            </div>
            <Button
              className="mt-3 w-full"
              disabled={active}
              onClick={() => input.current?.click()}
            >
              <Upload aria-hidden="true" />
              {active ? 'Uploading…' : 'Select PDF files'}
            </Button>
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
            {transfers.length > 0 && (
              <ul className="mt-4 space-y-3" aria-live="polite">
                {transfers.map((item) => (
                  <li key={item.id} className="min-w-0">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="min-w-0 break-all">
                        {item.file.name}
                      </span>
                      <span className="text-muted-foreground">
                        {item.status === 'done'
                          ? 'Uploaded'
                          : item.status === 'uploading'
                            ? item.percent === 100
                              ? 'Checking PDF…'
                              : `${item.percent}% uploaded`
                            : item.status === 'waiting'
                              ? 'Waiting'
                              : 'Upload failed'}
                      </span>
                      {item.status === 'failed' && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={active}
                          onClick={() => {
                            setTransfers((previous) =>
                              previous.filter(
                                (transfer) => transfer.id !== item.id
                              )
                            );
                            void upload([item.file]);
                          }}
                        >
                          Retry upload
                        </Button>
                      )}
                    </div>
                    {item.error && (
                      <Alert variant="destructive" className="mt-2">
                        <AlertDescription>{item.error}</AlertDescription>
                      </Alert>
                    )}
                    {item.status === 'uploading' && (
                      <progress
                        className="mt-2 h-2 w-full"
                        max={100}
                        value={item.percent}
                        aria-label={`Uploading ${item.file.name}`}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
      {(query.isError || refreshError) && (
        <Alert
          variant="destructive"
          className="mb-5 flex flex-wrap items-center gap-3"
        >
          <AlertDescription>
            {refreshError || 'Could not refresh scan status.'}
          </AlertDescription>
          <Button variant="outline" onClick={() => void query.refetch()}>
            Reload scans
          </Button>
        </Alert>
      )}
      {batches.length ? (
        <div className="border-t">
          {batches.map((batch) => (
            <ScanBatchCard
              key={batch.id}
              assignmentId={assignment.id}
              batch={batch}
              versions={versions}
            />
          ))}
        </div>
      ) : (
        <p className="py-8 text-muted-foreground">
          Uploaded scans will appear here, together with their proposed
          submissions and processing status.
        </p>
      )}
    </>
  );
}
