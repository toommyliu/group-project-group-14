import { usePreference } from '@/hooks/use-preference';
import { ChevronDown, Download, ExternalLink } from 'lucide-react';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

export function PdfActionButton({
  url,
  filename,
}: {
  url?: string;
  filename: string;
}) {
  const [action, setAction] = usePreference('pdfAction');

  return (
    <div className="inline-flex" role="group" aria-label="PDF actions">
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs"
        disabled={!url}
        nativeButton={!url}
        render={
          url ? (
            <a
              href={url}
              download={action === 'download' ? filename : undefined}
              target={action === 'open' ? '_blank' : undefined}
              rel={action === 'open' ? 'noopener noreferrer' : undefined}
            />
          ) : undefined
        }
      >
        {action === 'download' ? 'Download PDF' : 'Open in new tab'}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger
          disabled={!url}
          render={
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="-ms-px"
            />
          }
          aria-label="Choose PDF action"
        >
          <ChevronDown aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem
            disabled={!url}
            render={<a href={url} download={filename} />}
            onClick={() => setAction('download')}
          >
            <Download aria-hidden="true" />
            Download PDF
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!url}
            render={<a href={url} target="_blank" rel="noopener noreferrer" />}
            onClick={() => setAction('open')}
          >
            <ExternalLink aria-hidden="true" />
            Open in new tab
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
