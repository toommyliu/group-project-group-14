import type { DocumentEdit } from '@/api/queries';
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

type Version = NonNullable<DocumentEdit['versions']>[number];

export function VersionEditor({
  versions,
  selected,
  disabled,
  onChange,
  onSelect,
}: {
  versions: Version[];
  selected: string;
  disabled: boolean;
  onChange: (versions: Version[]) => void;
  onSelect: (key: string) => void;
}) {
  const options = versions.map((version) => ({
    value: version.key,
    label: version.label || 'Unnamed version',
  }));
  return (
    <div className="space-y-3">
      {versions.length > 1 && (
        <div className="flex items-center gap-3 text-sm">
          <span>Editing version</span>
          <Select
            items={options}
            value={selected}
            disabled={disabled}
            onValueChange={(value) => {
              if (value !== null) onSelect(value);
            }}
          >
            <SelectTrigger aria-label="Editing version">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {options.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <details className="space-y-3 rounded-lg border p-3">
        <summary className="cursor-pointer py-1 font-medium">Versions</summary>
        <p className="text-sm text-muted-foreground">
          Name each version and add its questions below. A single version can be
          unnamed.
        </p>
        {versions.map((version, index) => (
          <div key={version.key} className="flex items-end gap-2">
            <Label className="block min-w-0 flex-1 space-y-1 text-sm leading-normal">
              <span>Version {index + 1} name</span>
              <Input
                value={version.label}
                disabled={disabled}
                required={versions.length > 1}
                onChange={(event) =>
                  onChange(
                    versions.map((item) =>
                      item.key === version.key
                        ? { ...item, label: event.target.value }
                        : item
                    )
                  )
                }
              />
            </Label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled || versions.length === 1}
              aria-label={`Remove version ${version.label || index + 1}`}
              onClick={() => {
                const next = versions.filter(
                  (item) => item.key !== version.key
                );
                onChange(next);
                if (selected === version.key) onSelect(next[0].key);
              }}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => {
            const named = versions.map((item) => ({
              ...item,
              label: item.label.trim() || 'A',
            }));
            let number = 0;
            let label: string;
            do {
              label =
                number < 26
                  ? String.fromCharCode(65 + number)
                  : String(number + 1);
              number += 1;
            } while (named.some((item) => item.label === label));
            const version = {
              key: crypto.randomUUID(),
              original_label: null,
              label,
            };
            onChange([...named, version]);
            onSelect(version.key);
          }}
        >
          Add version
        </Button>
      </details>
    </div>
  );
}
