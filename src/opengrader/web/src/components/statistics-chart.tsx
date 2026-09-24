import { useMemo, useRef, useState } from 'react';
import { Chart } from '@tanstack/charts/react';
import { Copy, Ellipsis, ExternalLink } from 'lucide-react';
import {
  statisticsChartDefinition,
  type StatisticsBar,
} from '@/lib/statistics-chart';
import { copyChartImage, openChartImage } from '@/lib/chart-image';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { ErrorAlert } from './error-alert';

export function StatisticsChart({
  rows,
  unit,
  label,
  histogram = false,
}: {
  rows: StatisticsBar[];
  unit: 'percent' | 'submissions';
  label: string;
  histogram?: boolean;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const definition = useMemo(
    () => statisticsChartDefinition(rows, unit, histogram),
    [rows, unit, histogram]
  );

  async function exportImage(action: 'copy' | 'open') {
    if (!chartRef.current || busy) return;
    setBusy(true);
    setStatus('');
    setError('');
    try {
      if (action === 'copy') {
        await copyChartImage(chartRef.current);
        setStatus('Image copied');
      } else {
        await openChartImage(chartRef.current);
      }
    } catch (cause) {
      setError(
        cause instanceof Error && cause.name !== 'NotAllowedError'
          ? cause.message
          : 'Could not copy the image. Allow clipboard access or open the image in a new tab.'
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative pt-8">
      <div className="absolute end-0 top-0 flex items-center gap-2">
        <span role="status" className="text-xs text-muted-foreground">
          {status}
        </span>
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger
              render={
                <DropdownMenuTrigger
                  disabled={busy}
                  aria-label={`Image actions for ${label.toLowerCase()}`}
                  render={
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-muted-foreground"
                    />
                  }
                />
              }
            >
              <Ellipsis aria-hidden="true" />
            </TooltipTrigger>
            <TooltipContent>Chart image</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuItem onClick={() => void exportImage('copy')}>
              <Copy aria-hidden="true" /> Copy image
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => void exportImage('open')}>
              <ExternalLink aria-hidden="true" /> Open image in new tab
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="max-w-full overflow-x-auto">
        <div
          ref={chartRef}
          style={{ minWidth: `${Math.max(18, rows.length * 4)}rem` }}
        >
          <Chart
            definition={definition}
            height={280}
            ariaLabel={label}
            ariaDescription="The data is also available in the table below."
            tabIndex={-1}
            className="tabular-nums"
          />
        </div>
      </div>
      {error && <ErrorAlert error={error} className="mt-3" />}
    </div>
  );
}
