import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, FileText, LoaderCircle } from 'lucide-react';
import {
  analyzeScan,
  processScan,
  reviewScan,
  scanBusy,
  scansQuery,
  scanUrl,
  type ScanBatch,
  type ScanMode,
} from '@/api/scans';
import { errorMessage } from '@/lib/error-message';
import { Button, buttonVariants } from './ui/button';
import { Input } from './ui/input';
import { ScanReview } from './scan-review';
import { ScanActions } from './scan-actions';
import { Label } from './ui/label';
import { ErrorAlert } from '@/components/error-alert';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const scanModes = [
  { value: 'automatic', label: 'Detect first pages automatically' },
  { value: 'fixed', label: 'Fixed pages per submission' },
  { value: 'individual', label: 'This PDF is one submission' },
] satisfies { value: ScanMode; label: string }[];

const statusLabels = {
  uploaded: 'Uploaded',
  checking: 'Checking scan',
  review: 'Needs review',
  queued: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Needs attention',
  removed: 'Removed',
};

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
  const [open, setOpen] = useState(batch.status === 'uploaded');
  const [mode, setMode] = useState<ScanMode>(batch.mode);
  const [pages, setPages] = useState(
    batch.pages_per_submission?.toString() ?? ''
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [autoGrade, setAutoGrade] = useState(batch.auto_grade);
  const [version, setVersion] = useState(batch.assignment_version ?? '');
  const [groupVersions, setGroupVersions] = useState<
    Record<string, string | null>
  >({});
  const busy = pending || scanBusy(batch);
  const hasFindings =
    batch.findings.length > 0 ||
    batch.groups.some((group) => group.scan_warnings.length > 0);
  const ready = batch.groups.filter(
    (group) => group.submission_id !== null
  ).length;

  async function run(action: () => Promise<ScanBatch>) {
    if (busy) return;
    setPending(true);
    setError('');
    try {
      const updated = await action();
      setDirty(false);
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
    <article className="border-b py-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <FileText
          className="size-5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1 basis-44">
          <h2 className="text-base font-medium">
            <a
              href={`${scanUrl(assignmentId, batch.id)}/pdf`}
              target="_blank"
              rel="noreferrer"
              className="group/pdf-link inline-flex max-w-full items-center gap-1 underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-4"
              aria-label={`Open ${batch.filename} in a new tab`}
            >
              <span className="min-w-0 break-all">{batch.filename}</span>
              <ArrowUpRight
                className="size-4 shrink-0 opacity-0 group-hover/pdf-link:opacity-100 group-focus-visible/pdf-link:opacity-100"
                aria-hidden="true"
              />
            </a>
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {batch.page_count} pages ·{' '}
            {batch.groups.length
              ? `${batch.groups.length} submissions · ${ready} ready`
              : 'No submissions created'}
          </p>
        </div>
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          {scanBusy(batch) && (
            <LoaderCircle
              className="size-4 motion-safe:animate-spin"
              aria-hidden="true"
            />
          )}
          {statusLabels[batch.status]}
        </span>
        <Button
          variant="outline"
          aria-expanded={open}
          aria-controls={`scan-${batch.id}`}
          onClick={() => setOpen(!open)}
        >
          {open
            ? 'Hide details'
            : batch.status === 'review'
              ? 'Review split'
              : 'Show details'}
        </Button>
        <ScanActions
          assignmentId={assignmentId}
          batch={batch}
          disabled={busy || dirty}
          onPendingChange={setPending}
        />
      </div>
      <p className="mt-3 text-sm text-muted-foreground" role="status">
        {batch.message}
      </p>
      {error && <ErrorAlert className="mt-3" error={error} />}
      <div hidden={!open} id={`scan-${batch.id}`} className="mt-6 space-y-6">
        {!batch.started && batch.status !== 'removed' && (
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void run(() =>
                analyzeScan(
                  assignmentId,
                  batch,
                  mode,
                  mode !== 'individual' && pages ? Number(pages) : null
                )
              );
            }}
          >
            <div className="grid max-w-full min-w-0 gap-2">
              <Label htmlFor={`mode-${batch.id}`}>Separate submissions</Label>
              <Select
                items={scanModes}
                value={mode}
                disabled={busy}
                onValueChange={(value) => {
                  if (
                    value === 'automatic' ||
                    value === 'fixed' ||
                    value === 'individual'
                  )
                    setMode(value);
                }}
              >
                <SelectTrigger id={`mode-${batch.id}`} className="max-w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {scanModes.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {mode !== 'individual' && (
              <div className="grid gap-2">
                <Label htmlFor={`pages-${batch.id}`}>
                  {mode === 'fixed'
                    ? 'Scanned pages per submission'
                    : 'Expected pages per submission'}
                </Label>
                <Input
                  id={`pages-${batch.id}`}
                  className="w-36"
                  type="number"
                  min="1"
                  max={mode === 'fixed' ? batch.page_count : undefined}
                  step="1"
                  required={mode === 'fixed'}
                  aria-describedby={
                    mode === 'automatic' ? `pages-help-${batch.id}` : undefined
                  }
                  value={pages}
                  disabled={busy}
                  onChange={(event) => setPages(event.target.value)}
                />
                {mode === 'automatic' && (
                  <p
                    id={`pages-help-${batch.id}`}
                    className="text-sm text-muted-foreground"
                  >
                    Optional. Flags incomplete scans without changing the
                    detected groups.
                  </p>
                )}
              </div>
            )}
            <Button type="submit" variant="outline" disabled={busy || dirty}>
              {busy
                ? 'Checking…'
                : batch.groups.length
                  ? 'Recalculate split'
                  : 'Check scan'}
            </Button>
          </form>
        )}
        {batch.findings.length > 0 && (
          <details className="rounded-md border p-4">
            <summary className="cursor-pointer font-medium">
              {batch.findings.length} scan findings
            </summary>
            <p className="mt-3 text-muted-foreground">
              Inspect these pages before processing. Blank pages may be
              unanswered work; they are never removed automatically.
            </p>
            <ul className="mt-3 max-h-64 space-y-2 overflow-auto">
              {batch.findings.map((finding, index) => (
                <li key={index}>
                  {finding.page && (
                    <a
                      className="underline underline-offset-4"
                      target="_blank"
                      rel="noreferrer"
                      href={`${scanUrl(assignmentId, batch.id)}/pages/${finding.page}?size=large`}
                    >
                      Page {finding.page}
                    </a>
                  )}
                  {finding.page ? ': ' : ''}
                  {finding.detail}
                </li>
              ))}
            </ul>
          </details>
        )}
        {batch.groups.length > 0 &&
          !batch.started &&
          batch.status !== 'removed' &&
          !scanBusy(batch) && (
            <ScanReview
              key={batch.revision}
              assignmentId={assignmentId}
              batch={batch}
              busy={pending}
              onDirty={() => setDirty(true)}
              onSave={async (groups, excluded) => {
                await run(() =>
                  reviewScan(assignmentId, batch, groups, excluded)
                );
              }}
            />
          )}
        {batch.started && (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {batch.groups.map((group, index) => (
              <li key={group.key} className="min-w-0 rounded-md border p-3">
                <p className="font-medium">
                  Submission {index + 1} · {group.pages.length} pages
                </p>
                <p className="mt-1 text-sm break-words text-muted-foreground">
                  Scan pages {group.pages.map((page) => page.number).join(', ')}
                </p>
                {group.scan_warnings.map((warning) => (
                  <p key={warning} className="mt-2 text-sm">
                    {warning}
                  </p>
                ))}
                {group.submission_id !== null ? (
                  <Link
                    to="/assignments/$assignmentId/grade"
                    params={{ assignmentId }}
                    search={{ submission: group.submission_id }}
                    className={buttonVariants({
                      variant: 'outline',
                      className: 'mt-3',
                    })}
                  >
                    Grade submission
                  </Link>
                ) : (
                  <p className="mt-2 text-sm">
                    {group.error || 'Waiting to process'}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        {batch.groups.length > 0 &&
          batch.status !== 'ready' &&
          batch.status !== 'removed' && (
            <div className="space-y-4 border-t pt-5">
              {versions.length > 0 && (
                <div className="grid max-w-xs gap-2">
                  <Label htmlFor={`version-${batch.id}`}>
                    Default exam version
                  </Label>
                  <Select
                    items={[
                      { value: '', label: 'Detect from each submission' },
                      ...versions.map((value) => ({
                        value,
                        label: `Version ${value}`,
                      })),
                    ]}
                    value={version}
                    disabled={busy}
                    onValueChange={(value) => {
                      if (value !== null) setVersion(value);
                    }}
                  >
                    <SelectTrigger
                      id={`version-${batch.id}`}
                      className="max-w-full"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="start" alignItemWithTrigger={false}>
                      <SelectItem value="">
                        Detect from each submission
                      </SelectItem>
                      {versions.map((item) => (
                        <SelectItem key={item} value={item}>
                          Version {item}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              {versions.length > 0 && (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {batch.groups.map(
                    (group, index) =>
                      group.submission_id === null && (
                        <div key={group.key} className="grid min-w-0 gap-2">
                          <Label htmlFor={`version-${group.key}`}>
                            Submission {index + 1} version
                          </Label>
                          <Select
                            items={[
                              { value: '', label: 'Use default' },
                              ...versions.map((value) => ({
                                value,
                                label: `Version ${value}`,
                              })),
                            ]}
                            value={
                              (group.key in groupVersions
                                ? groupVersions[group.key]
                                : group.assignment_version) ?? ''
                            }
                            disabled={busy}
                            onValueChange={(value) => {
                              if (value !== null)
                                setGroupVersions((previous) => ({
                                  ...previous,
                                  [group.key]: value || null,
                                }));
                            }}
                          >
                            <SelectTrigger
                              id={`version-${group.key}`}
                              className="w-full"
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent
                              align="start"
                              alignItemWithTrigger={false}
                            >
                              <SelectItem value="">Use default</SelectItem>
                              {versions.map((value) => (
                                <SelectItem key={value} value={value}>
                                  Version {value}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <p className="text-xs text-muted-foreground">
                            Scan pages{' '}
                            {group.pages.map((page) => page.number).join(', ')}
                          </p>
                        </div>
                      )
                  )}
                </div>
              )}
              <Label className="flex min-h-11 items-center gap-3">
                <input
                  type="checkbox"
                  checked={autoGrade}
                  disabled={busy}
                  onChange={(event) => setAutoGrade(event.target.checked)}
                />
                Automatically score matching answers
              </Label>
              {hasFindings && (
                <Label className="flex min-h-11 items-center gap-3">
                  <input
                    type="checkbox"
                    checked={acknowledge}
                    disabled={busy}
                    onChange={(event) => setAcknowledge(event.target.checked)}
                  />
                  I reviewed the scan findings and page groups.
                </Label>
              )}
              <Button
                disabled={busy || dirty}
                onClick={() =>
                  void run(() =>
                    processScan(
                      assignmentId,
                      batch,
                      acknowledge,
                      autoGrade,
                      version,
                      groupVersions
                    )
                  )
                }
              >
                {busy
                  ? 'Processing…'
                  : batch.started
                    ? 'Retry unfinished submissions'
                    : 'Create submissions'}
              </Button>
              <p className="text-sm text-muted-foreground">
                Processing continues on the server after you leave. Once
                processing starts, the split is fixed and existing submissions
                are preserved.
              </p>
            </div>
          )}
      </div>
    </article>
  );
}
