import { useQuery } from '@tanstack/react-query';
import { cn } from 'cn';
import { Check, ChevronsUpDown, Library } from 'lucide-react';
import { workspaceBase } from '@/api/client';
import { coursesQuery, workspaceQuery } from '@/api/workspace';
import { CourseAvatar } from './course-avatar';
import { Button } from './ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuLinkItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

export function CourseSwitcher({ className }: { className?: string }) {
  const current = useQuery({
    ...workspaceQuery,
    enabled: Boolean(workspaceBase),
  }).data;
  const courses = useQuery(coursesQuery).data;
  if (workspaceBase && !current) return null;
  const name = current ? current.name || 'Untitled course' : 'All courses';
  const others =
    courses?.filter(
      (course) => course.available && course.id !== current?.id
    ) ?? [];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            className={cn(
              'h-9 max-w-full min-w-0 shrink justify-start gap-2 px-2 max-sm:shrink-0',
              className
            )}
          />
        }
        aria-label={`${name}, switch course`}
        title={name}
      >
        {current ? (
          <CourseAvatar id={current.id} name={name} size="sm" />
        ) : (
          <Library className="mx-1 text-muted-foreground" aria-hidden="true" />
        )}
        <span className={cn('min-w-0 truncate', current && 'max-sm:hidden')}>
          {name}
        </span>
        <ChevronsUpDown className="text-muted-foreground" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-72 max-w-[calc(100vw-2rem)]"
      >
        <DropdownMenuGroup className="max-h-80 overflow-y-auto overscroll-contain">
          <DropdownMenuLabel className="sticky top-0 z-10 bg-popover">
            Courses
          </DropdownMenuLabel>
          {current && (
            <DropdownMenuItem
              className="min-h-9 text-sm"
              aria-current="page"
              title={name}
            >
              <CourseAvatar id={current.id} name={name} size="sm" />
              <span className="min-w-0 flex-1 truncate">{name}</span>
              <Check aria-hidden="true" />
            </DropdownMenuItem>
          )}
          {others.map((course) => (
            <DropdownMenuLinkItem
              key={course.id}
              href={course.url}
              className="min-h-9 text-sm"
              title={course.name}
            >
              <CourseAvatar id={course.id} name={course.name} size="sm" />
              <span className="min-w-0 flex-1 truncate">{course.name}</span>
            </DropdownMenuLinkItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLinkItem
          href="/courses"
          className="min-h-9 text-sm"
          aria-current={current ? undefined : 'page'}
        >
          <Library className="mx-1" aria-hidden="true" />
          <span className="flex-1">All courses</span>
          {!current && <Check aria-hidden="true" />}
        </DropdownMenuLinkItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
