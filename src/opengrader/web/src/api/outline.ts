import { queryOptions } from '@tanstack/react-query';
import { client, readData, uploadPdf, workspaceUrl } from './client';
import type { Assignment } from './queries';

async function fetchOutline(
  assignmentId: string,
  version: string | undefined,
  signal?: AbortSignal
) {
  return readData(
    await client.GET('/api/assignments/{assignment_id}/outline', {
      params: {
        path: { assignment_id: assignmentId },
        query: { version: version ?? null },
      },
      signal,
    })
  );
}

export type AssignmentOutline = Awaited<ReturnType<typeof fetchOutline>>;
export type Outline = AssignmentOutline['outline'];
export type RegionBox = NonNullable<Outline['name_box']>;

export function outlineQuery(assignmentId: string, version?: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'outline', version ?? null],
    queryFn: ({ signal }) => fetchOutline(assignmentId, version, signal),
  });
}

export async function saveOutline(
  assignmentId: string,
  current: AssignmentOutline,
  outline: Outline
) {
  return readData(
    await client.PUT('/api/assignments/{assignment_id}/outline', {
      params: { path: { assignment_id: assignmentId } },
      body: { version: current.version, revision: current.revision, outline },
    })
  );
}

export async function retryRecut(assignmentId: string, version: string) {
  return readData(
    await client.POST('/api/assignments/{assignment_id}/outline/recut', {
      params: { path: { assignment_id: assignmentId }, query: { version } },
    })
  );
}

export function templateUrl(assignmentId: string, outline: AssignmentOutline) {
  const query = new URLSearchParams({
    version: outline.version,
    v: outline.template?.sha256 ?? '',
  });
  return workspaceUrl(
    `/api/assignments/${encodeURIComponent(assignmentId)}/template?${query}`
  );
}

export function overlayUrl(
  assignmentId: string,
  version: string,
  revision: string,
  page: number
) {
  const query = new URLSearchParams({
    version,
    page: String(page),
    v: revision,
  });
  return workspaceUrl(
    `/api/assignments/${encodeURIComponent(assignmentId)}/outline/overlay?${query}`
  );
}

export function createAssignmentFromTemplate(
  title: string,
  file: File,
  progress?: (percent: number) => void
) {
  const query = new URLSearchParams({ title, filename: file.name });
  return uploadPdf<Assignment>(
    'POST',
    `/api/assignments/from-template?${query}`,
    file,
    progress
  );
}

export function replaceTemplate(
  assignmentId: string,
  version: string,
  file: File,
  progress?: (percent: number) => void
) {
  const query = new URLSearchParams({ filename: file.name, version });
  return uploadPdf<AssignmentOutline>(
    'PUT',
    `/api/assignments/${encodeURIComponent(assignmentId)}/template?${query}`,
    file,
    progress
  );
}

export async function generateTemplate(assignmentId: string, version: string) {
  return readData(
    await client.POST('/api/assignments/{assignment_id}/template/generate', {
      params: { path: { assignment_id: assignmentId }, query: { version } },
    })
  );
}

export async function addVersion(
  assignmentId: string,
  label: string,
  copyFrom: string | null
) {
  return readData(
    await client.POST('/api/assignments/{assignment_id}/versions', {
      params: { path: { assignment_id: assignmentId } },
      body: { label, copy_from: copyFrom },
    })
  );
}

export async function removeVersion(assignmentId: string, version: string) {
  return readData(
    await client.DELETE('/api/assignments/{assignment_id}/versions/{version}', {
      params: { path: { assignment_id: assignmentId, version } },
    })
  );
}
