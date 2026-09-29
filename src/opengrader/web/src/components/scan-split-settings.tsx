import { useState, type ReactNode } from 'react';
import type { ScanBatch, ScanMode } from '@/api/scans';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

const scanModes = [
  { value: 'automatic', label: 'Find each first page automatically' },
  { value: 'fixed', label: 'Same number of pages each' },
  { value: 'individual', label: 'Whole PDF is one submission' },
] satisfies { value: ScanMode; label: string }[];

export function ScanSplitSettings({
  batch,
  busy,
  submitLabel,
  note,
  onSubmit,
}: {
  batch: ScanBatch;
  busy: boolean;
  submitLabel: string;
  note?: ReactNode;
  onSubmit: (mode: ScanMode, pages: number | null) => void;
}) {
  const [mode, setMode] = useState<ScanMode>(batch.mode);
  const [pages, setPages] = useState(
    batch.pages_per_submission?.toString() ?? ''
  );

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(mode, mode !== 'individual' && pages ? Number(pages) : null);
      }}
    >
      <div className="grid gap-2">
        <Label htmlFor={`mode-${batch.id}`}>How to split this PDF</Label>
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
          <SelectTrigger id={`mode-${batch.id}`} className="w-full">
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
              ? 'Pages per submission'
              : 'Expected pages per submission'}
          </Label>
          <Input
            id={`pages-${batch.id}`}
            className="w-28"
            type="number"
            inputMode="numeric"
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
              className="text-xs text-muted-foreground"
            >
              Optional. Flags submissions with missing or extra pages.
            </p>
          )}
        </div>
      )}
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
      <Button type="submit" className="justify-self-start" disabled={busy}>
        {submitLabel}
      </Button>
    </form>
  );
}
