import { useState } from 'react';
import { ArrowLeft, ArrowRight, RotateCw } from 'lucide-react';
import { scanUrl, type ScanBatch, type ScanPage } from '@/api/scans';
import { Button } from './ui/button';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const rotations = [0, 90, 180, 270] as const;

export function ScanReview({
  assignmentId,
  batch,
  busy,
  onSave,
  onDirty,
}: {
  assignmentId: string;
  batch: ScanBatch;
  busy: boolean;
  onSave: (groups: ScanPage[][], excluded: number[]) => Promise<void>;
  onDirty: () => void;
}) {
  const [groups, setGroups] = useState(() =>
    batch.groups.map((group) => group.pages)
  );
  const [excluded, setExcluded] = useState(batch.excluded_pages);
  const [selected, setSelected] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [dirty, setDirty] = useState(false);
  const group = groups[selected] ?? [];

  function update(next: ScanPage[][]) {
    const nonempty = next.filter((pages) => pages.length);
    setGroups(nonempty);
    setSelected(Math.min(selected, Math.max(0, nonempty.length - 1)));
    setPageOffset(0);
    setDirty(true);
    onDirty();
  }

  function move(page: ScanPage, destination: number) {
    const next = groups.map((pages) =>
      pages.filter((item) => item.number !== page.number)
    );
    if (destination === groups.length) next.push([page]);
    else next[destination] = [...next[destination], page];
    update(next);
  }

  function exclude(page: ScanPage) {
    setExcluded([...excluded, page.number]);
    update(
      groups.map((pages) => pages.filter((item) => item.number !== page.number))
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid max-w-full min-w-0 gap-2">
          <Label htmlFor={`group-${batch.id}`}>Review submission</Label>
          <Select
            items={groups.map((pages, value) => ({
              value,
              label: `Submission ${value + 1} · ${pages.length} pages`,
            }))}
            value={selected}
            onValueChange={(value) => {
              if (value === null) return;
              setSelected(value);
              setPageOffset(0);
            }}
          >
            <SelectTrigger id={`group-${batch.id}`} className="max-w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start" alignItemWithTrigger={false}>
              {groups.map((pages, index) => (
                <SelectItem key={index} value={index}>
                  Submission {index + 1} · {pages.length} pages
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          disabled={selected === 0}
          onClick={() => {
            setSelected(selected - 1);
            setPageOffset(0);
          }}
        >
          Previous submission
        </Button>
        <Button
          variant="outline"
          disabled={selected >= groups.length - 1}
          onClick={() => {
            setSelected(selected + 1);
            setPageOffset(0);
          }}
        >
          Next submission
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        Check who each page belongs to. Page numbers refer to the original scan.
        Open a page to inspect it at full size.
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))] gap-5">
        {group.slice(pageOffset, pageOffset + 12).map((page, offset) => {
          const index = pageOffset + offset;
          return (
            <div
              key={page.number}
              className="min-w-0 space-y-3 rounded-lg border p-3"
            >
              <a
                href={`${scanUrl(assignmentId, batch.id)}/pages/${page.number}?large=true`}
                target="_blank"
                rel="noreferrer"
                className="flex h-52 items-center justify-center overflow-hidden rounded bg-muted/40"
                aria-label={`Open original scan page ${page.number}`}
              >
                <img
                  src={`${scanUrl(assignmentId, batch.id)}/pages/${page.number}`}
                  alt={`Scan page ${page.number}`}
                  loading="lazy"
                  className="max-h-full max-w-full object-contain outline outline-black/10 dark:outline-white/10"
                  style={{ transform: `rotate(${page.rotation}deg)` }}
                />
              </a>
              <p className="font-medium">
                Page {page.number}
                {page.rotation ? ` · rotated ${page.rotation}°` : ''}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="icon"
                  variant="outline"
                  aria-label={`Move page ${page.number} earlier`}
                  disabled={busy || index === 0}
                  onClick={() => {
                    const pages = [...group];
                    [pages[index - 1], pages[index]] = [
                      pages[index],
                      pages[index - 1],
                    ];
                    update(
                      groups.map((item, groupIndex) =>
                        groupIndex === selected ? pages : item
                      )
                    );
                  }}
                >
                  <ArrowLeft aria-hidden="true" />
                </Button>
                <Button
                  size="icon"
                  variant="outline"
                  aria-label={`Move page ${page.number} later`}
                  disabled={busy || index === group.length - 1}
                  onClick={() => {
                    const pages = [...group];
                    [pages[index], pages[index + 1]] = [
                      pages[index + 1],
                      pages[index],
                    ];
                    update(
                      groups.map((item, groupIndex) =>
                        groupIndex === selected ? pages : item
                      )
                    );
                  }}
                >
                  <ArrowRight aria-hidden="true" />
                </Button>
                <Button
                  size="icon"
                  variant="outline"
                  aria-label={`Rotate page ${page.number} clockwise`}
                  disabled={busy}
                  onClick={() => {
                    update(
                      groups.map((pages) =>
                        pages.map((item) =>
                          item.number === page.number
                            ? {
                                ...item,
                                rotation:
                                  rotations[
                                    (rotations.indexOf(item.rotation ?? 0) +
                                      1) %
                                      4
                                  ],
                              }
                            : item
                        )
                      )
                    );
                  }}
                >
                  <RotateCw aria-hidden="true" />
                </Button>
              </div>
              <div className="grid gap-2">
                <Label htmlFor={`move-${batch.id}-${page.number}`}>
                  Move page to
                </Label>
                <Select
                  items={[
                    ...groups.map((_, value) => ({
                      value,
                      label: `Submission ${value + 1}`,
                    })),
                    { value: groups.length, label: 'New submission' },
                  ]}
                  value={selected}
                  disabled={busy}
                  onValueChange={(value) => {
                    if (value !== null) move(page, value);
                  }}
                >
                  <SelectTrigger
                    id={`move-${batch.id}-${page.number}`}
                    className="w-full"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="start" alignItemWithTrigger={false}>
                    {groups.map((_, groupIndex) => (
                      <SelectItem key={groupIndex} value={groupIndex}>
                        Submission {groupIndex + 1}
                      </SelectItem>
                    ))}
                    <SelectItem value={groups.length}>
                      New submission
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {index > 0 && (
                <Button
                  variant="outline"
                  disabled={busy}
                  className="w-full"
                  onClick={() =>
                    update([
                      ...groups.slice(0, selected),
                      group.slice(0, index),
                      group.slice(index),
                      ...groups.slice(selected + 1),
                    ])
                  }
                >
                  Split before this page
                </Button>
              )}
              <Button
                variant="ghost"
                disabled={busy || groups.flat().length <= 1}
                className="w-full"
                onClick={() => exclude(page)}
              >
                Exclude page
              </Button>
            </div>
          );
        })}
      </div>
      {group.length > 12 && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            disabled={pageOffset === 0}
            onClick={() => setPageOffset(Math.max(0, pageOffset - 12))}
          >
            Earlier pages
          </Button>
          <span>
            Pages {pageOffset + 1}–{Math.min(pageOffset + 12, group.length)} of{' '}
            {group.length}
          </span>
          <Button
            variant="outline"
            disabled={pageOffset + 12 >= group.length}
            onClick={() => setPageOffset(pageOffset + 12)}
          >
            Later pages
          </Button>
        </div>
      )}
      {excluded.length > 0 && (
        <div className="space-y-2">
          <p className="font-medium">Excluded pages</p>
          <p className="text-muted-foreground">
            These stay in the original PDF. Restore a page to the selected
            submission if needed.
          </p>
          <div className="flex flex-wrap gap-2">
            {excluded.map((number) => (
              <Button
                key={number}
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setExcluded(excluded.filter((page) => page !== number));
                  update(
                    groups.map((pages, index) =>
                      index === selected
                        ? [...pages, { number, rotation: 0 }]
                        : pages
                    )
                  );
                }}
              >
                Restore page {number}
              </Button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={busy || !dirty}
          onClick={() => void onSave(groups, excluded)}
        >
          {busy ? 'Saving…' : 'Save page groups'}
        </Button>
        <span role="status" className="text-muted-foreground">
          {dirty
            ? 'Save your changes before creating submissions.'
            : `${groups.length} proposed submissions. Page groups saved.`}
        </span>
      </div>
    </div>
  );
}
