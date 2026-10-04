import { queryOptions } from '@tanstack/react-query';
import { client, readData, serverClient } from './client';
import type { components } from './schema';

export type WorkspaceInfo = components['schemas']['WorkspaceInfo'];
export type FolderSessionInfo = components['schemas']['FolderSessionInfo'];
export type FolderSyncResult = components['schemas']['FolderSyncResult'];

export type CourseSummary = components['schemas']['WorkspaceSummary'];

export const coursesQuery = queryOptions({
  queryKey: ['workspaces'],
  queryFn: async ({ signal }) =>
    readData(await serverClient.GET('/api/workspaces', { signal })),
  refetchOnWindowFocus: true,
});

export const workspaceQuery = queryOptions({
  queryKey: ['workspace'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/api/workspace', { signal })),
});

export async function syncFolderSession() {
  return readData(await client.POST('/api/folder-session/sync'));
}

export async function convertFolderSession() {
  return readData(await client.POST('/api/folder-session/convert'));
}
