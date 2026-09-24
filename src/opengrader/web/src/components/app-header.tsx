import { Link } from '@tanstack/react-router';
import { ModeToggle } from './mode-toggle';
import { AuthControl } from './auth-control';

export function AppHeader() {
  return (
    <header className="border-b">
      <div className="mx-auto flex min-h-16 max-w-[90rem] items-center gap-6 px-4 sm:px-8 lg:px-14">
        <Link
          to="/"
          className="flex min-h-11 items-center text-base font-semibold"
        >
          Opengrader
        </Link>
        <Link
          to="/workspace"
          className="flex min-h-11 flex-1 items-center text-muted-foreground hover:text-foreground"
        >
          Assignments
        </Link>
        <AuthControl />
        <ModeToggle />
      </div>
    </header>
  );
}
