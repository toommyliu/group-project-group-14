import { CourseSwitcher } from './course-switcher';

export function CourseBreadcrumb() {
  return (
    <nav
      aria-label="Breadcrumb"
      className="mb-4 flex min-w-0 shrink-0 items-center gap-1"
    >
      <CourseSwitcher className="-ms-2" />
    </nav>
  );
}
