import { queryOptions } from '@tanstack/react-query';
import { client, readData, serverClient } from './client';
import type { components } from './schema';

export type WorkspaceInfo = components['schemas']['WorkspaceInfo'];
export type FolderSessionInfo = components['schemas']['FolderSessionInfo'];
export type FolderSyncResult = components['schemas']['FolderSyncResult'];

export type CourseSummary = components['schemas']['WorkspaceSummary'];
export type Course = components['schemas']['CourseDetails'];
export type CourseForm = components['schemas']['CourseForm'];
export type Term = NonNullable<Course['term']>;

export const terms: readonly Term[] = ['winter', 'spring', 'summer', 'fall'];

export function termName(term: Term) {
  return term.charAt(0).toUpperCase() + term.slice(1);
}

export function courseTerm({ term, year }: Pick<Course, 'term' | 'year'>) {
  return [term && termName(term), year].filter(Boolean).join(' ');
}

export function courseTitle({ number, name }: Pick<Course, 'number' | 'name'>) {
  return name ? `${number}: ${name}` : number;
}

export function currentTerm(today = new Date()): { term: Term; year: number } {
  const month = today.getMonth() + 1;
  return {
    term: month <= 5 ? 'spring' : month <= 7 ? 'summer' : 'fall',
    year: today.getFullYear(),
  };
}

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

export async function createCourse(body: CourseForm) {
  return readData(await serverClient.POST('/api/workspaces', { body }));
}

export async function updateCourse(id: string, body: CourseForm) {
  return readData(
    await serverClient.PUT('/api/workspaces/{workspace_id}', {
      params: { path: { workspace_id: id } },
      body,
    })
  );
}

export async function removeCourse(id: string) {
  await serverClient.DELETE('/api/workspaces/{workspace_id}', {
    params: { path: { workspace_id: id } },
  });
}

export async function syncFolderSession() {
  return readData(await client.POST('/api/folder-session/sync'));
}

export async function convertFolderSession() {
  return readData(await client.POST('/api/folder-session/convert'));
}
