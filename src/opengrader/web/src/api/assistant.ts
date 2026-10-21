import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type AssistantOverview = components['schemas']['AssistantOverview'];
export type AssistantCopy = components['schemas']['AssistantCopy'];
export type AssistantSuggestion = components['schemas']['AssistantSuggestion'];
export type KeyFileChoice = 'update' | 'leave';

export const assistantQuery = (assignmentId: string) =>
  queryOptions({
    queryKey: ['assignments', assignmentId, 'assistant'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET('/api/assignments/{assignment_id}/assistant', {
          params: { path: { assignment_id: assignmentId } },
          signal,
        })
      ),
  });

export const suggestionsQuery = (assignmentId: string, name: string) =>
  queryOptions({
    queryKey: ['assignments', assignmentId, 'assistant', name, 'suggestions'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET(
          '/api/assignments/{assignment_id}/assistant/copies/{name}/suggestions',
          { params: { path: { assignment_id: assignmentId, name } }, signal }
        )
      ),
  });

export const mergePreviewQuery = (assignmentId: string, name: string) =>
  queryOptions({
    queryKey: ['assignments', assignmentId, 'assistant', name, 'merge'],
    queryFn: async ({ signal }) =>
      readData(
        await client.GET(
          '/api/assignments/{assignment_id}/assistant/copies/{name}/merge',
          { params: { path: { assignment_id: assignmentId, name } }, signal }
        )
      ),
    staleTime: 0,
  });

export async function makeCopy(
  assignmentId: string,
  purpose: 'grading' | 'rubrics'
) {
  return readData(
    await client.POST('/api/assignments/{assignment_id}/assistant/copies', {
      params: { path: { assignment_id: assignmentId } },
      body: { purpose, with_graded: false },
    })
  );
}

export async function mergeCopy(assignmentId: string, name: string) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/assistant/copies/{name}/merge',
      { params: { path: { assignment_id: assignmentId, name } } }
    )
  );
}

export async function deleteCopy(assignmentId: string, name: string) {
  await client.DELETE(
    '/api/assignments/{assignment_id}/assistant/copies/{name}',
    { params: { path: { assignment_id: assignmentId, name } } }
  );
}

export async function acceptFix(
  assignmentId: string,
  name: string,
  suggestionId: string,
  keyFile: KeyFileChoice | null
) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/assistant/copies/{name}/suggestions/{suggestion_id}/accept',
      {
        params: {
          path: {
            assignment_id: assignmentId,
            name,
            suggestion_id: suggestionId,
          },
        },
        body: { key_file: keyFile },
      }
    )
  );
}

export async function dismissFix(
  assignmentId: string,
  name: string,
  suggestionId: string
) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/assistant/copies/{name}/suggestions/{suggestion_id}/dismiss',
      {
        params: {
          path: {
            assignment_id: assignmentId,
            name,
            suggestion_id: suggestionId,
          },
        },
      }
    )
  );
}

export async function makeKeyFolder(assignmentId: string) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/assistant/key-folders',
      { params: { path: { assignment_id: assignmentId } } }
    )
  );
}

export async function storeKeyFolder(
  assignmentId: string,
  name: string,
  keyFile: KeyFileChoice | null,
  replace = false
) {
  return readData(
    await client.POST(
      '/api/assignments/{assignment_id}/assistant/key-folders/use',
      {
        params: { path: { assignment_id: assignmentId } },
        body: { name, key_file: keyFile, replace },
      }
    )
  );
}
