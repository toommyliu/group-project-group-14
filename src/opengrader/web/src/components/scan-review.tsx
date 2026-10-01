import { useRef, useState } from 'react';
import { DragDropProvider } from '@dnd-kit/react';
import { isSortable, useSortable } from '@dnd-kit/react/sortable';
import {
  Accessibility,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/dom';
import {
  CircleAlert,
  Ellipsis,
  Merge,
  RotateCw,
  Scissors,
  Settings2,
  Undo2,
  ZoomIn,
} from 'lucide-react';
import { cn } from 'cn';
import {
  analyzeScan,
  processScan,
  reviewScan,
  scanPageUrl,
  type ScanBatch,
  type ScanPage,
} from '@/api/scans';
import {
  excludePage,
  groupsFrom,
  mergeWithPrevious,
  movePage,
  nonempty,
  pageRanges,
  pageSignature,
  restorePage,
  rotatePages,
  splitBefore,
  thumbnailTransform,
  type PageGroup,
} from '@/lib/scan-groups';
import {
  DEFAULT_VERSION_LABEL,
  DETECT_VERSION_LABEL,
  inheritedVersionLabel,
} from '@/lib/scan-versions';
import { ScanPageDialog, type PreviewPage } from './scan-page-dialog';
import { ScanSplitSettings } from './scan-split-settings';
import { VersionSelect } from './scan-version-select';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

type Run = (action: () => Promise<ScanBatch>) => Promise<void>;

const accessibility = Accessibility.configure({
  announcements: {
    dragstart: ({ operation: { source } }: DragStartEvent) =>
      source
        ? `Picked up page ${source.id}. Use arrow keys to move it, then Space to drop.`
        : undefined,
    dragover: () => undefined,
    dragend: ({ operation: { source }, canceled }: DragEndEvent) =>
      canceled ? 'Move canceled.' : `Moved page ${source?.id}.`,
  },
});

function planKey(groups: PageGroup[], excluded: number[]) {
  return `${groups
    .map((group) =>
      group.pages.map((page) => `${page.number}:${page.rotation}`).join(',')
    )
    .join('|')}#${excluded.join(',')}`;
}

/** Split editor: every proposed submission is a row of page thumbnails. */
export function ScanReview({
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
  run: Run;
  apply: (batch: ScanBatch) => void;
}) {
  const [revision, setRevision] = useState(batch.revision);
  const [groups, setGroups] = useState(() => groupsFrom(batch));
  const [excluded, setExcluded] = useState(batch.excluded_pages);
  const [autoGrade, setAutoGrade] = useState(batch.auto_grade);
  const [defaultVersion, setDefaultVersion] = useState(
    batch.assignment_version ?? ''
  );
  const [checked, setChecked] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const snapshot = useRef<PageGroup[] | null>(null);

  // A saved or re-split scan replaces the local plan; keep chosen versions.
  if (revision !== batch.revision) {
    const chosen = new Map(
      groups.map((group) => [pageSignature(group.pages), group.version])
    );
    setRevision(batch.revision);
    setGroups(
      groupsFrom(batch).map((group) => ({
        ...group,
        version: chosen.get(pageSignature(group.pages)) ?? group.version,
      }))
    );
    setExcluded(batch.excluded_pages);
  }

  const saved = planKey(groupsFrom(batch), batch.excluded_pages);
  const dirty = planKey(groups, excluded) !== saved;
  const warnings = new Map(
    batch.groups.map((group) => [
      pageSignature(group.pages),
      group.scan_warnings,
    ])
  );
  const flagged = new Map<number, string[]>();
  for (const finding of batch.findings)
    if (finding.page)
      flagged.set(finding.page, [
        ...(flagged.get(finding.page) ?? []),
        finding.detail,
      ]);
  const general = batch.findings.filter((finding) => !finding.page);
  const needsCheck =
    batch.findings.length > 0 ||
    groups.some((group) => warnings.get(pageSignature(group.pages))?.length);
  const previewPages: PreviewPage[] = [
    ...groups.flatMap((group, index) =>
      group.pages.map((page) => ({
        ...page,
        label: `Submission ${index + 1} · Scan page ${page.number}`,
      }))
    ),
    ...excluded.map((number) => ({
      number,
      rotation: 0 as const,
      label: `Excluded · Scan page ${number}`,
    })),
  ];
  const pageCount = groups.reduce((sum, group) => sum + group.pages.length, 0);

  function edit(next: PageGroup[]) {
    setGroups(next);
    setChecked(false);
  }

  function open(number: number) {
    setPreview(previewPages.findIndex((page) => page.number === number));
  }

  async function create() {
    const chosen = new Map(
      groups.map((group) => [pageSignature(group.pages), group.version])
    );
    await run(async () => {
      let current = batch;
      if (dirty) {
        current = await reviewScan(
          assignmentId,
          batch,
          groups.map((group) => group.pages),
          excluded
        );
        apply(current);
        // Saving can surface new length warnings; show them before creating.
        const warned =
          current.findings.length > 0 ||
          current.groups.some((group) => group.scan_warnings.length);
        if (warned && !checked) return current;
      }
      return processScan(
        assignmentId,
        current,
        checked,
        autoGrade,
        defaultVersion,
        Object.fromEntries(
          current.groups.map((group) => [
            group.key,
            chosen.get(pageSignature(group.pages)) ?? null,
          ])
        )
      );
    });
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">
            {groups.length} {groups.length === 1 ? 'submission' : 'submissions'}
          </span>{' '}
          from {pageCount} {pageCount === 1 ? 'page' : 'pages'}
          {excluded.length > 0 && ` · ${excluded.length} excluded`}. Click
          between pages to split, or drag pages to move them.
        </p>
        <Popover open={settingsOpen} onOpenChange={setSettingsOpen}>
          <PopoverTrigger
            render={<Button variant="outline" size="lg" disabled={busy} />}
          >
            <Settings2 aria-hidden="true" />
            Re-split
          </PopoverTrigger>
          <PopoverContent>
            <ScanSplitSettings
              batch={batch}
              busy={busy}
              submitLabel="Re-split scan"
              note={
                dirty
                  ? 'Replaces the current groups and discards your unsaved changes.'
                  : 'Replaces the current groups.'
              }
              onSubmit={(mode, pages) => {
                setSettingsOpen(false);
                void run(() => analyzeScan(assignmentId, batch, mode, pages));
              }}
            />
          </PopoverContent>
        </Popover>
      </div>

      {batch.findings.length > 0 && (
        <div className="border border-chart-4/40 bg-chart-4/10 p-3 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <CircleAlert
              className="size-4 shrink-0 text-chart-4"
              aria-hidden="true"
            />
            Check {batch.findings.length === 1 ? 'this' : 'these'}{' '}
            {batch.findings.length}{' '}
            {batch.findings.length === 1 ? 'finding' : 'findings'} before
            creating submissions
          </p>
          <ul className="mt-2 grid max-h-40 gap-1 overflow-auto pl-6">
            {[...flagged].map(([page, details]) => (
              <li key={page}>
                <button
                  type="button"
                  className="underline underline-offset-4 hover:text-muted-foreground"
                  onClick={() => open(page)}
                >
                  Page {page}
                </button>
                : {details.join(' ')}
              </li>
            ))}
            {general.map((finding, index) => (
              <li key={index}>{finding.detail}</li>
            ))}
          </ul>
          <p className="mt-2 pl-6 text-xs text-muted-foreground">
            Blank pages may be unanswered work, so they are never removed
            automatically.
          </p>
        </div>
      )}

      <DragDropProvider
        plugins={(defaults) =>
          defaults.map((plugin) =>
            plugin === Accessibility ? accessibility : plugin
          )
        }
        onDragStart={() => {
          snapshot.current = groups;
        }}
        onDragOver={(event) => {
          const { source, target } = event.operation;
          // Reorder React state instead of letting dnd-kit move DOM nodes.
          event.preventDefault();
          if (!isSortable(source) || !isSortable(target)) return;
          if (source.id === target.id) return;
          // Entering another group past a page's center drops after it.
          const dragged = event.operation.shape?.current.center;
          const over = target.shape?.center;
          const after = !!dragged && !!over && dragged.x > over.x;
          setChecked(false);
          setGroups((current) => {
            const to = current.findIndex((group) =>
              group.pages.some((page) => page.number === target.id)
            );
            if (to === -1) return current;
            const pages = current[to].pages;
            const sameGroup = pages.some((page) => page.number === source.id);
            const index = pages.findIndex((page) => page.number === target.id);
            return movePage(
              current,
              Number(source.id),
              to,
              index + (!sameGroup && after ? 1 : 0),
              true
            );
          });
        }}
        onDragEnd={(event) => {
          const initial = snapshot.current;
          snapshot.current = null;
          setGroups((current) =>
            event.canceled && initial ? initial : nonempty(current)
          );
        }}
      >
        <ol className="divide-y border bg-background">
          {groups.map((group, groupIndex) => (
            <GroupRow
              key={group.id}
              assignmentId={assignmentId}
              batchId={batch.id}
              group={group}
              groupIndex={groupIndex}
              groupCount={groups.length}
              versions={versions}
              defaultVersion={defaultVersion}
              warnings={warnings.get(pageSignature(group.pages)) ?? []}
              flagged={flagged}
              busy={busy}
              onPreview={open}
              onSplit={(pageIndex) =>
                edit(splitBefore(groups, groupIndex, pageIndex))
              }
              onMerge={() => edit(mergeWithPrevious(groups, groupIndex))}
              onRotate={(numbers) => edit(rotatePages(groups, numbers))}
              onMove={(number, destination) =>
                edit(
                  movePage(
                    groups,
                    number,
                    destination,
                    groups[destination]?.pages.length ?? 0
                  )
                )
              }
              onExclude={(number) => {
                setExcluded([...excluded, number].sort((a, b) => a - b));
                edit(excludePage(groups, number));
              }}
              onVersion={(version) =>
                setGroups(
                  groups.map((item) =>
                    item.id === group.id ? { ...item, version } : item
                  )
                )
              }
            />
          ))}
        </ol>
      </DragDropProvider>

      {excluded.length > 0 && (
        <section
          className="grid gap-2"
          aria-labelledby={`excluded-${batch.id}`}
        >
          <h3 id={`excluded-${batch.id}`} className="text-sm font-medium">
            Excluded pages
          </h3>
          <p className="text-xs text-muted-foreground">
            These stay in the original PDF but are left out of every submission.
          </p>
          <ul className="flex flex-wrap gap-3">
            {excluded.map((number) => (
              <li key={number} className="grid w-20 gap-1">
                <button
                  type="button"
                  className="aspect-[17/22] overflow-hidden border bg-white opacity-60 hover:opacity-100"
                  onClick={() => open(number)}
                  aria-label={`Preview scan page ${number}`}
                >
                  <img
                    src={scanPageUrl(assignmentId, batch.id, number)}
                    alt=""
                    loading="lazy"
                    className="size-full object-contain"
                  />
                </button>
                <Button
                  variant="ghost"
                  size="xs"
                  disabled={busy}
                  aria-label={`Restore page ${number}`}
                  onClick={() => {
                    setExcluded(excluded.filter((page) => page !== number));
                    edit(restorePage(groups, number));
                  }}
                >
                  <Undo2 aria-hidden="true" />
                  Page {number}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t bg-card px-4 py-3">
        <Label className="flex min-h-8 items-center gap-2 text-sm leading-normal">
          <Checkbox
            checked={autoGrade}
            disabled={busy}
            onCheckedChange={setAutoGrade}
          />
          Auto-grade matching answers
        </Label>
        {versions.length > 0 && (
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
        {needsCheck && (
          <Label className="flex min-h-8 items-center gap-2 text-sm leading-normal">
            <Checkbox
              checked={checked}
              disabled={busy}
              onCheckedChange={setChecked}
            />
            I reviewed the warnings
          </Label>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {dirty && (
            <>
              <span className="text-xs text-muted-foreground" role="status">
                Unsaved changes
              </span>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setGroups(
                    groupsFrom(batch).map((group) => ({
                      ...group,
                      version:
                        groups.find(
                          (item) =>
                            pageSignature(item.pages) ===
                            pageSignature(group.pages)
                        )?.version ?? group.version,
                    }))
                  );
                  setExcluded(batch.excluded_pages);
                }}
              >
                Discard
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    reviewScan(
                      assignmentId,
                      batch,
                      groups.map((group) => group.pages),
                      excluded
                    )
                  )
                }
              >
                Save split
              </Button>
            </>
          )}
          <Button
            disabled={busy || !groups.length || (needsCheck && !checked)}
            onClick={() => void create()}
          >
            {busy
              ? 'Working…'
              : `Create ${groups.length} ${groups.length === 1 ? 'submission' : 'submissions'}`}
          </Button>
        </div>
      </div>

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

function GroupRow({
  assignmentId,
  batchId,
  group,
  groupIndex,
  groupCount,
  versions,
  defaultVersion,
  warnings,
  flagged,
  busy,
  onPreview,
  onSplit,
  onMerge,
  onRotate,
  onMove,
  onExclude,
  onVersion,
}: {
  assignmentId: string;
  batchId: string;
  group: PageGroup;
  groupIndex: number;
  groupCount: number;
  versions: string[];
  defaultVersion: string;
  warnings: string[];
  flagged: Map<number, string[]>;
  busy: boolean;
  onPreview: (number: number) => void;
  onSplit: (pageIndex: number) => void;
  onMerge: () => void;
  onRotate: (numbers: ReadonlySet<number>) => void;
  onMove: (number: number, destination: number) => void;
  onExclude: (number: number) => void;
  onVersion: (version: string | null) => void;
}) {
  const title = `Submission ${groupIndex + 1}`;
  return (
    <li
      className="grid gap-3 p-4 [contain-intrinsic-size:auto_11rem] [content-visibility:auto] @min-[48rem]:grid-cols-[11rem_minmax(0,1fr)]"
      aria-label={title}
    >
      <div className="grid content-start gap-2 @min-[48rem]:sticky @min-[48rem]:top-4 @min-[48rem]:self-start">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="text-xs text-muted-foreground">
            {group.pages.length} {group.pages.length === 1 ? 'page' : 'pages'} ·
            p. {pageRanges(group.pages.map((page) => page.number))}
          </p>
        </div>
        {warnings.map((warning) => (
          <p
            key={warning}
            className="flex gap-1.5 text-xs text-chart-4"
            role="status"
          >
            <CircleAlert
              className="mt-px size-3.5 shrink-0"
              aria-hidden="true"
            />
            {warning}
          </p>
        ))}
        {versions.length > 0 && (
          <VersionSelect
            id={`version-${group.id}`}
            label="Version"
            versions={versions}
            value={group.version ?? ''}
            emptyLabel={inheritedVersionLabel(defaultVersion)}
            disabled={busy}
            stacked
            onChange={onVersion}
          />
        )}
        <div className="flex flex-wrap gap-1">
          {groupIndex > 0 && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={onMerge}
              aria-label={`Merge ${title} with submission ${groupIndex}`}
            >
              <Merge aria-hidden="true" />
              Merge up
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() =>
              onRotate(new Set(group.pages.map((page) => page.number)))
            }
            aria-label={`Rotate every page of ${title}`}
          >
            <RotateCw aria-hidden="true" />
            Rotate all
          </Button>
        </div>
      </div>
      {!group.pages.length ? (
        <p className="flex min-h-20 items-center border border-dashed px-3 text-xs text-muted-foreground">
          Empty. This submission is removed when you drop the page.
        </p>
      ) : (
        <ol className="flex flex-wrap content-start items-start gap-y-6 pb-4">
          {group.pages.map((page, index) => (
            <PageThumb
              key={page.number}
              assignmentId={assignmentId}
              batchId={batchId}
              page={page}
              index={index}
              group={group}
              groupIndex={groupIndex}
              groupCount={groupCount}
              flags={flagged.get(page.number)}
              busy={busy}
              onPreview={onPreview}
              onSplit={onSplit}
              onRotate={onRotate}
              onMove={onMove}
              onExclude={onExclude}
            />
          ))}
        </ol>
      )}
    </li>
  );
}

function PageThumb({
  assignmentId,
  batchId,
  page,
  index,
  group,
  groupIndex,
  groupCount,
  flags,
  busy,
  onPreview,
  onSplit,
  onRotate,
  onMove,
  onExclude,
}: {
  assignmentId: string;
  batchId: string;
  page: ScanPage;
  index: number;
  group: PageGroup;
  groupIndex: number;
  groupCount: number;
  flags?: string[];
  busy: boolean;
  onPreview: (number: number) => void;
  onSplit: (pageIndex: number) => void;
  onRotate: (numbers: ReadonlySet<number>) => void;
  onMove: (number: number, destination: number) => void;
  onExclude: (number: number) => void;
}) {
  const { ref, isDragging } = useSortable({
    id: page.number,
    index,
    group: group.id,
    type: 'scan-page',
    accept: 'scan-page',
    disabled: busy,
  });
  const label = `Page ${page.number}`;

  return (
    <li
      ref={ref}
      aria-label={flags ? `${label}, flagged: ${flags.join(' ')}` : label}
      className={cn('flex outline-none', isDragging && 'z-10 opacity-60')}
    >
      {index > 0 ? (
        <button
          type="button"
          disabled={busy}
          className="group/split relative w-6 cursor-pointer self-stretch outline-none disabled:cursor-default"
          aria-label={`Split before page ${page.number}`}
          onClick={() => onSplit(index)}
        >
          <span className="absolute inset-y-0 left-1/2 -translate-x-1/2 border-l-2 border-dashed border-transparent group-hover/split:border-foreground group-focus-visible/split:border-ring group-disabled/split:border-transparent" />
          <span className="absolute top-1/2 left-1/2 flex size-6 -translate-1/2 items-center justify-center rounded-full bg-foreground text-background opacity-0 group-hover/split:opacity-100 group-focus-visible/split:opacity-100 group-disabled/split:opacity-0">
            <Scissors className="size-3.5 -rotate-90" aria-hidden="true" />
          </span>
          <span className="absolute top-full left-1/2 mt-0.5 -translate-x-1/2 text-xs font-medium whitespace-nowrap opacity-0 group-hover/split:opacity-100 group-focus-visible/split:opacity-100 group-disabled/split:opacity-0">
            Split here
          </span>
        </button>
      ) : (
        <span className="w-6" aria-hidden="true" />
      )}
      <div
        className={cn(
          'group/page relative w-24 cursor-grab border bg-white active:cursor-grabbing',
          flags && 'border-chart-4 ring-1 ring-chart-4'
        )}
      >
        <img
          src={scanPageUrl(assignmentId, batchId, page.number)}
          alt=""
          loading="lazy"
          draggable={false}
          className="aspect-[17/22] w-full object-contain"
          style={{ transform: thumbnailTransform(page.rotation) }}
          onClick={() => onPreview(page.number)}
        />
        <span className="pointer-events-none absolute bottom-0 left-0 bg-foreground px-1.5 text-xs text-background tabular-nums">
          {page.number}
        </span>
        {flags && (
          <CircleAlert
            className="pointer-events-none absolute right-0.5 bottom-0.5 size-4 fill-card text-chart-4"
            aria-hidden="true"
          />
        )}
        <div className="absolute top-0.5 right-0.5 flex gap-0.5 bg-background opacity-0 group-focus-within/page:opacity-100 group-hover/page:opacity-100 has-aria-expanded:opacity-100 pointer-coarse:opacity-100">
          <Button
            variant="outline"
            size="icon-xs"
            disabled={busy}
            aria-label={`Preview page ${page.number}`}
            onClick={() => onPreview(page.number)}
          >
            <ZoomIn aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="icon-xs"
            disabled={busy}
            aria-label={`Rotate page ${page.number} clockwise`}
            onClick={() => onRotate(new Set([page.number]))}
          >
            <RotateCw aria-hidden="true" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger
              disabled={busy}
              render={
                <Button
                  variant="outline"
                  size="icon-xs"
                  aria-label={`More actions for page ${page.number}`}
                />
              }
            >
              <Ellipsis aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              {index > 0 && (
                <DropdownMenuItem onClick={() => onSplit(index)}>
                  Start a new submission here
                </DropdownMenuItem>
              )}
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>Move to</DropdownMenuSubTrigger>
                <DropdownMenuSubContent className="max-h-72 w-48 overflow-auto">
                  {Array.from({ length: groupCount }, (_, destination) => (
                    <DropdownMenuItem
                      key={destination}
                      disabled={destination === groupIndex}
                      onClick={() => onMove(page.number, destination)}
                    >
                      Submission {destination + 1}
                    </DropdownMenuItem>
                  ))}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => onMove(page.number, groupCount)}
                  >
                    New submission
                  </DropdownMenuItem>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onExclude(page.number)}
              >
                Exclude page
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </li>
  );
}
