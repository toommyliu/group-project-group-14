import { useAssignmentNavigation } from '@/hooks/use-assignment-navigation';
import { AssignmentSwitcher } from './assignment-switcher';
import { CourseSwitcher } from './course-switcher';

export function CourseBreadcrumb() {
  const { assignment } = useAssignmentNavigation();
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex min-w-0 flex-1 items-center gap-1"
    >
      <CourseSwitcher
        className={assignment ? '-ms-2 sm:max-w-[min(18rem,45%)]' : '-ms-2'}
      />
      {assignment && (
        <>
          <span aria-hidden="true" className="shrink-0 text-muted-foreground">
            /
          </span>
          <AssignmentSwitcher assignment={assignment} />
        </>
      )}
    </nav>
  );
}
