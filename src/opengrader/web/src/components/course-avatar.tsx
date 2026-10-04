import { cn } from 'cn';

const colors = [
  'bg-chart-1/15 text-chart-1',
  'bg-chart-2/15 text-chart-2',
  'bg-chart-3/15 text-chart-3',
  'bg-chart-4/15 text-chart-4',
  'bg-chart-5/15 text-chart-5',
];

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => Array.from(word)[0])
    .join('')
    .toLocaleUpperCase();
}

function color(id: string) {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}

export function CourseAvatar({
  id,
  name,
  size = 'md',
  muted = false,
  className,
}: {
  id: string;
  name: string;
  size?: 'sm' | 'md';
  muted?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex shrink-0 items-center justify-center rounded-md leading-none font-semibold select-none',
        size === 'sm' ? 'size-6 text-[0.625rem]' : 'size-10 text-sm',
        muted ? 'bg-muted text-muted-foreground' : color(id),
        className
      )}
    >
      {initials(name)}
    </span>
  );
}
