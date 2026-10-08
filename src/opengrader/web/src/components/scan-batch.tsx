import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  FileText,
  LoaderCircle,
} from 'lucide-react';
import { cn } from 'cn';
import {
  analyzeScan,
  processScan,
  restoreScan,
  scanBusy,
  scanPageUrl,
  scansQuery,
  type ScanBatch,
} from '@/api/scans';
import { errorMessage } from '@/lib/error-message';
import { pageRanges, thumbnailTransform } from '@/lib/scan-groups';
import {
  DEFAULT_VERSION_LABEL,
  DETECT_VERSION_LABEL,
  inheritedVersionLabel,
} from '@/lib/scan-versions';
import { ErrorAlert } from '@/components/error-alert';
import { Button, buttonVariants } from './ui/button';
import { ScanActions } from './scan-actions';
import { ScanPageDialog, type PreviewPage } from './scan-page-dialog';
import { ScanReview } from './scan-review';
import { ScanSplitSettings } from './scan-split-settings';
import { VersionSelect } from './scan-version-select';

const uploadedAt = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

function ScanStatus({ batch }: { batch: ScanBatch }) {
  const total = batch.groups.length;
  const done = batch.groups.filter(
    (group) => group.submission_id !== null
  ).length;
  const [icon, label, tone] = {
    uploaded: [null, 'Not split yet', 'text-muted-foreground'],
    checking: [LoaderCircle, 'Splitting…', 'text-muted-foreground'],
    review: [CircleAlert, 'Needs review', 'text-chart-4'],
    queued: [LoaderCircle, 'Queued', 'text-muted-foreground'],
    processing: [
      LoaderCircle,
      `Creating ${done} of ${total}`,
      'text-muted-foreground',
    ],
    ready: [
      CircleCheck,
      `${total} ${total === 1 ? 'submission' : 'submissions'}`,
      'text-chart-1',
    ],
    failed: [CircleX, 'Needs attention', 'text-destructive'],
    removed: [null, 'Removed', 'text-muted-foreground'],
  }[batch.status] as [typeof LoaderCircle | null, string, string];
  const Icon = icon;
  return (
    <span
      className={cn(
        'flex shrink-0 items-center gap-1.5 text-sm tabular-nums',
        tone
      )}
    >
      {Icon && (
        <Icon
          className={cn(
            'size-4',
            Icon === LoaderCircle && 'motion-safe:animate-spin'
          )}
          aria-hidden="true"
        />
      )}
      {label}
    </span>
  );
}

export function ScanBatchCard({
  assignmentId,
  batch,
  versions,
}: {
  assignmentId: string;
  batch: ScanBatch;
  versions: string[];
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(
    batch.status !== 'ready' && batch.status !== 'removed'
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const busy = pending || scanBusy(batch);
  const bodyId = `scan-${batch.id}`;

  function apply(updated: ScanBatch) {
    client.setQueryData(
      scansQuery(assignmentId).queryKey,
      (previous) =>
        previous && {
          ...previous,
          batches: previous.batches.map((item) =>
            item.id === updated.id ? updated : item
          ),
        }
    );
  }

  async function run(action: () => Promise<ScanBatch>) {
    if (busy) return;
    setPending(true);
    setError('');
    try {
      apply(await action());
      await client.invalidateQueries({
        queryKey: ['assignments', assignmentId, 'submissions'],
      });
    } catch (cause) {
      setError(errorMessage(cause, 'Could not update this scan. Try again.'));
    } finally {
      setPending(false);
    }
  }

  return (
    <article
      className={cn(
        'border bg-card',
        batch.status === 'removed' && 'opacity-70'
      )}
    >
      <div className="flex min-h-14 items-center gap-3 py-1.5 pr-1.5 pl-3">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-3 self-stretch text-left outline-none focus-visible:ring-1 focus-visible:ring-ring"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
        >
          <ChevronRight
            className={cn(
              'size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none',
              open && 'rotate-90'
            )}
            aria-hidden="true"
          />
          <FileText
            className="size-5 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <span className="min-w-0">
            <span className="block truncate font-medium">{batch.filename}</span>
            <span className="block text-xs text-muted-foreground">
              {batch.page_count} {batch.page_count === 1 ? 'page' : 'pages'} ·
              Uploaded {uploadedAt.format(new Date(batch.created_at))}
            </span>
          </span>
        </button>
        <ScanStatus batch={batch} />
        {batch.status === 'review' && !open && (
          <Button variant="outline" size="lg" onClick={() => setOpen(true)}>
            Review split
          </Button>
        )}
        <ScanActions
          assignmentId={assignmentId}
          batch={batch}
          disabled={busy}
          onPendingChange={setPending}
        />
      </div>
      <div
        id={bodyId}
        hidden={!open}
        className="@container grid gap-4 border-t px-4 pt-4 pb-4 has-[.sticky]:pb-0"
      >
        {error && <ErrorAlert error={error} />}
        {open && (
          <ScanBody
            assignmentId={assignmentId}
            batch={batch}
            versions={versions}
            busy={busy}
            run={run}
            apply={apply}
          />
        )}
      </div>
    </article>
  );
}

function ScanBody({
  assignmentId,
  batch,
  versions,
  busy,
  run,
  apply,
}: {
  assignmentId: string;
  batch: ScanBatch;
  versions: string[];
  busy: boolean;
  run: (action: () => Promise<ScanBatch>) => Promise<void>;
  apply: (batch: ScanBatch) => void;
}) {
  if (batch.status === 'removed')
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          This scan is out of intake. Its PDF and any submissions made from it
          are kept.
        </p>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void run(() => restoreScan(assignmentId, batch))}
        >
          Restore scan
        </Button>
      </div>
    );

  if (batch.status === 'checking')
    return (
      <p
        className="flex items-center gap-2 text-sm text-muted-foreground"
        role="status"
      >
        <LoaderCircle
          className="size-4 shrink-0 motion-safe:animate-spin"
          aria-hidden="true"
        />
        Finding where each submission starts. This can take a moment for large
        scans.
      </p>
    );

  if (!batch.started && batch.status === 'review')
    return (
      <ScanReview
        assignmentId={assignmentId}
        batch={batch}
        versions={versions}
        busy={busy}
        run={run}
        apply={apply}
      />
    );

  if (!batch.started)
    return (
      <div className="grid max-w-sm gap-4">
        {batch.status === 'failed' ? (
          <ErrorAlert error={batch.message} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Split this PDF into one submission per student. You can review and
            adjust the split before anything is created.
          </p>
        )}
        <ScanSplitSettings
          batch={batch}
          busy={busy}
          submitLabel={
            batch.status === 'failed' ? 'Try again' : 'Split into submissions'
          }
          onSubmit={(mode, pages) =>
            void run(() => analyzeScan(assignmentId, batch, mode, pages))
          }
        />
      </div>
    );

  return (
    <ScanSubmissions
      assignmentId={assignmentId}
      batch={batch}
      versions={versions}
      busy={busy}
      run={run}
    />
  );
}

function ScanSubmissions({
  assignmentId,
  batch,
  versions,
  busy,
  run,
}: {
  assignmentId: string;
  batch: ScanBatch;
  versions: string[];
  busy: boolean;
  run: (action: () => Promise<ScanBatch>) => Promise<void>;
}) {
  const [defaultVersion, setDefaultVersion] = useState(
    batch.assignment_version ?? versions[0] ?? ''
  );
  const [groupVersions, setGroupVersions] = useState<
    Record<string, string | null>
  >({});
  const total = batch.groups.length;
  const done = batch.groups.filter(
    (group) => group.submission_id !== null
  ).length;
  const active = scanBusy(batch);
  const retrying = batch.status === 'failed' && versions.length > 0;
  const [preview, setPreview] = useState<number | null>(null);
  const title = (group: ScanBatch['groups'][number], index: number) =>
    group.student?.name ?? `Submission ${index + 1}`;
  const previewPages: PreviewPage[] = batch.groups.flatMap((group, index) =>
    group.pages.map((page) => ({
      ...page,
      label: `${title(group, index)} · Scan page ${page.number}`,
    }))
  );

  return (
    <div className="grid gap-4">
      {batch.status !== 'ready' && (
        <div className="grid gap-2">
          <p
            className={cn(
              'text-sm',
              batch.status === 'failed'
                ? 'text-destructive'
                : 'text-muted-foreground'
            )}
            role="status"
          >
            {batch.message}
          </p>
          <div
            className="h-1.5 overflow-hidden bg-muted"
            role="progressbar"
            aria-label="Submissions created"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
          >
            <div
              className="h-full bg-chart-1 transition-[width] motion-reduce:transition-none"
              style={{ width: `${total ? (done / total) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}
      <ol className="divide-y border bg-background">
        {batch.groups.map((group, index) => {
          const first = group.pages[0];
          const version =
            group.assignment_version ??
            group.matched_version ??
            batch.assignment_version;
          return (
            <li key={group.key} className="flex items-center gap-3 px-3 py-2">
              {first && (
                <button
                  type="button"
                  className="shrink-0 cursor-zoom-in"
                  aria-label={`View pages of ${title(group, index)}`}
                  onClick={() =>
                    setPreview(
                      previewPages.findIndex(
                        (page) => page.number === first.number
                      )
                    )
                  }
                >
                  <img
                    src={scanPageUrl(assignmentId, batch.id, first.number)}
                    alt=""
                    loading="lazy"
                    className="aspect-[17/22] w-9 border bg-white object-contain"
                    style={{ transform: thumbnailTransform(first.rotation) }}
                  />
                </button>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{title(group, index)}</p>
                <p className="truncate text-xs text-muted-foreground">
                  Scan pages{' '}
                  {pageRanges(group.pages.map((page) => page.number))}
                  {version && ` · Version ${version}`}
                </p>
                {group.scan_warnings.map((warning) => (
                  <p key={warning} className="text-xs text-chart-4">
                    {warning}
                  </p>
                ))}
              </div>
              {group.submission_id !== null ? (
                <Link
                  to="/assignments/$assignmentId/grade"
                  params={{ assignmentId }}
                  search={{ submission: group.submission_id }}
                  className={buttonVariants({ variant: 'outline', size: 'lg' })}
                >
                  Grade
                </Link>
              ) : group.error ? (
                <div className="grid max-w-72 justify-items-end gap-1.5">
                  <span className="text-right text-xs text-destructive">
                    {group.error}
                  </span>
                  {retrying && (
                    <VersionSelect
                      id={`version-${group.key}`}
                      label="Version"
                      versions={versions}
                      value={
                        (group.key in groupVersions
                          ? groupVersions[group.key]
                          : group.assignment_version) ?? ''
                      }
                      emptyLabel={inheritedVersionLabel(defaultVersion)}
                      disabled={busy}
                      onChange={(value) =>
                        setGroupVersions({
                          ...groupVersions,
                          [group.key]: value,
                        })
                      }
                    />
                  )}
                </div>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {active ? 'Waiting' : 'Not created'}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {batch.status === 'failed' && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {retrying && (
            <VersionSelect
              id={`version-${batch.id}`}
              label={DEFAULT_VERSION_LABEL}
              versions={versions}
              value={defaultVersion}
              emptyLabel={DETECT_VERSION_LABEL}
              disabled={busy}
              onChange={(value) => setDefaultVersion(value ?? '')}
            />
          )}
          <Button
            disabled={busy}
            onClick={() =>
              void run(() =>
                processScan(
                  assignmentId,
                  batch,
                  true,
                  defaultVersion,
                  groupVersions
                )
              )
            }
          >
            Retry {total - done} unfinished{' '}
            {total - done === 1 ? 'submission' : 'submissions'}
          </Button>
        </div>
      )}
      <ScanPageDialog
        assignmentId={assignmentId}
        batchId={batch.id}
        pages={previewPages}
        index={preview}
        onIndexChange={setPreview}
      />
    </div>
  );
}
