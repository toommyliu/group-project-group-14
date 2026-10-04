import { Outlet } from '@tanstack/react-router';
import { workspaceBase } from '@/api/client';
import Workspace from './workspace';
import { App } from '@/App';

export default function RootLayout() {
  if (workspaceBase) return <Workspace />;
  return (
    <App>
      <Outlet />
    </App>
  );
}
