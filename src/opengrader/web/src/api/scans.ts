import { queryOptions } from '@tanstack/react-query';
import type { components } from './schema';
import { client, readData, uploadPdf, workspaceUrl } from './client';

export type ScanBatch = components['schemas']['ScanBatch'];
export type ScanPage = ScanBatch['groups'][number]['pages'][number];
export type ScanMode = NonNullable<
  components['schemas']['AnalyzeScan']['mode']
>;

export function scansQuery(assignmentId: string) {
  return queryOptions({
    queryKey: ['assignments', assignmentId, 'scans'],
    staleTime: 0,
    // The destination loader shares this request when the scans page unmounts.
    queryFn: async () =>
      readData(
        await client.GET('/api/assignments/{assignment_id}/scans', {
          params: { path: { assignment_id: assignmentId } },
        })
      ),
    refetchInterval: (query) =>
      query.state.data?.batches.some(scanBusy) ? 1500 : false,
  });
}

export function scanBusy(batch: ScanBatch) {
  return (
    batch.status === 'checking' ||
    batch.status === 'queued' ||
    batch.status === 'processing'
  );
}

export function scanUrl(assignmentId: string, batchId: string) {
  return workspaceUrl(
    `/api/assignments/${encodeURIComponent(assignmentId)}/scans/${encodeURIComponent(batchId)}`
  );
}

export function scanPageUrl(
  assignmentId: string,
  batchId: string,
  page: number,
  size: 'small' | 'large' = 'small'
) {
  return `${scanUrl(assignmentId, batchId)}/pages/${page}${size === 'large' ? '?size=large' : ''}`;
}

export function uploadScan(
  assignmentId: string,
  file: File,
  progress?: (percent: number) => void,
  student?: { sid: string; version: string }
): Promise<ScanBatch> {
  const query = new URLSearchParams({ filename: file.name });
  if (student) query.set('student_id', student.sid);
  if (student?.version) query.set('assignment_version', student.version);
  return uploadPdf(
    'POST',
    `/api/assignments/${encodeURIComponent(assignmentId)}/scans?${query}`,
    file,
    progress
  );
}

export type StudentUpload = {
  batch: ScanBatch;
  group: ScanBatch['groups'][number];
};

export function studentUploads(batches: ScanBatch[]) {
  const latest = new Map<string, StudentUpload>();
  for (const batch of batches)
    if (batch.status !== 'removed')
      for (const group of batch.groups)
        if (group.student)
          latest.set(group.student.student_id, { batch, group });
  return latest;
}

export async function analyzeScan(
  assignmentId: string,
  batch: ScanBatch,
  mode: ScanMode,
  pages: number | null
) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/scans/{batch_id}/analyze',
      {
        params: { path: { assignment_id: assignmentId, batch_id: batch.id } },
        body: {
          expected_revision: batch.revision,
          mode,
          pages_per_submission: pages,
        },
      }
    )
  );
}

export async function reviewScan(
  assignmentId: string,
  batch: ScanBatch,
  groups: ScanPage[][],
  excluded: number[]
) {
  return readData(
    await client.PUT(
      '/api/assignments/{assignment_id}/scans/{batch_id}/groups',
      {
        params: { path: { assignment_id: assignmentId, batch_id: batch.id } },
        body: {
          expected_revision: batch.revision,
          groups,
          excluded_pages: excluded,
        },
      }
    )
  );
}

export async function processScan(
  assignmentId: string,
  batch: ScanBatch,
  acknowledge: boolean,
  version: string,
  groupVersions: Record<string, string | null> = {}
) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/scans/{batch_id}/process',
      {
        params: { path: { assignment_id: assignmentId, batch_id: batch.id } },
        body: {
          expected_revision: batch.revision,
          acknowledge_findings: acknowledge,
          assignment_version: version || null,
          group_versions: groupVersions,
        },
      }
    )
  );
}

export async function removeScan(assignmentId: string, batch: ScanBatch) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/scans/{batch_id}/remove',
      {
        params: { path: { assignment_id: assignmentId, batch_id: batch.id } },
        body: { expected_revision: batch.revision },
      }
    )
  );
}

export async function restoreScan(assignmentId: string, batch: ScanBatch) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/scans/{batch_id}/restore',
      {
        params: { path: { assignment_id: assignmentId, batch_id: batch.id } },
        body: { expected_revision: batch.revision },
      }
    )
  );
}

export async function deleteScan(assignmentId: string, batch: ScanBatch) {
  await client.DELETE('/api/assignments/{assignment_id}/scans/{batch_id}', {
    params: {
      path: { assignment_id: assignmentId, batch_id: batch.id },
      query: { expected_revision: batch.revision },
    },
  });
}
