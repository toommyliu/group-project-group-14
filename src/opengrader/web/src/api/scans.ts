import { queryOptions } from '@tanstack/react-query';
import type { components } from './schema';
import { ApiError, client, readData } from './client';

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
  return `/api/assignments/${encodeURIComponent(assignmentId)}/scans/${encodeURIComponent(batchId)}`;
}

export function uploadScan(
  assignmentId: string,
  file: File,
  progress: (percent: number) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(
      'POST',
      `/api/assignments/${encodeURIComponent(assignmentId)}/scans?filename=${encodeURIComponent(file.name)}`
    );
    request.setRequestHeader('Content-Type', 'application/pdf');
    request.responseType = 'json';
    request.upload.onprogress = (event) => {
      if (event.lengthComputable)
        progress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () =>
      reject(
        new Error(
          'Upload interrupted. Retry this file; completed uploads are preserved.'
        )
      );
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) return resolve();
      reject(new ApiError(request.status, request.response));
    };
    request.send(file);
  });
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
  autoGrade: boolean,
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
          auto_grade: autoGrade,
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
