import { Link, useMatchRoute } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronsUpDown, FileText } from 'lucide-react';
import { cn } from 'cn';
import { assignmentsQuery } from '@/api/queries';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuLinkItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const pages = [
  '/assignments/$assignmentId/scans',
  '/assignments/$assignmentId/grade',
  '/assignments/$assignmentId/statistics',
] as const;

export function AssignmentSwitcher({
  assignment,
  className,
}: {
  assignment: { id: string; title: string };
  className?: string;
}) {
  const assignments = useQuery(assignmentsQuery).data ?? [];
  const matchRoute = useMatchRoute();
  const page =
    pages.find((to) => matchRoute({ to })) ?? '/assignments/$assignmentId';
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            className={cn(
              'h-9 max-w-full min-w-0 shrink justify-start gap-2 px-2',
              className
            )}
          />
        }
        aria-label={`${assignment.title}, switch assignment`}
        title={assignment.title}
      >
        <span className="min-w-0 truncate">{assignment.title}</span>
        <ChevronsUpDown className="text-muted-foreground" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-72 max-w-[calc(100vw-2rem)]"
      >
        <DropdownMenuGroup className="max-h-80 overflow-y-auto overscroll-contain">
          <DropdownMenuLabel className="sticky top-0 z-10 bg-popover">
            Assignments
          </DropdownMenuLabel>
          {assignments.map((item) => {
            const current = item.id === assignment.id;
            return (
              <DropdownMenuLinkItem
                key={item.id}
                className="min-h-9 text-sm"
                aria-current={current ? 'page' : undefined}
                title={item.title}
                render={<Link to={page} params={{ assignmentId: item.id }} />}
              >
                <span className="min-w-0 flex-1 truncate">{item.title}</span>
                {current && <Check aria-hidden="true" />}
              </DropdownMenuLinkItem>
            );
          })}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLinkItem
          className="min-h-9 text-sm"
          render={<Link to="/assignments" />}
        >
          <FileText aria-hidden="true" />
          All assignments
        </DropdownMenuLinkItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
