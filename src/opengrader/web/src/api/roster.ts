import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';
import type { Submission } from './queries';

export type Student = components['schemas']['Student'];
export type StudentDetails = components['schemas']['StudentDetails'];
export type RosterImport = components['schemas']['RosterImport'];
export type RosterUpdate = components['schemas']['RosterUpdate'];

export const rosterQuery = queryOptions({
  queryKey: ['roster'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/api/roster', { signal })),
});

export async function addStudent(details: StudentDetails) {
  return readData(await client.POST('/api/roster/students', { body: details }));
}

export async function editStudent(
  student: Pick<Student, 'id' | 'revision'>,
  details: StudentDetails
) {
  return readData(
    await client.PUT('/api/roster/students/{student_id}', {
      params: { path: { student_id: student.id } },
      body: { ...details, expected_revision: student.revision },
    })
  );
}

export async function removeStudent(studentId: string) {
  await client.DELETE('/api/roster/students/{student_id}', {
    params: { path: { student_id: studentId } },
  });
}

const csv = {
  bodySerializer: (file: Blob) => file,
  headers: { 'Content-Type': 'text/csv' },
};

export async function previewRosterImport(file: File) {
  return readData(
    await client.POST('/api/roster/import/preview', { ...csv, body: file })
  );
}

export async function importRoster(file: File, preview: RosterImport) {
  return readData(
    await client.POST('/api/roster/import', {
      ...csv,
      body: file,
      params: { query: { expected: preview.fingerprint } },
    })
  );
}

export function studentName({
  first_name,
  last_name,
}: Pick<StudentDetails, 'first_name' | 'last_name'>) {
  return [first_name, last_name].filter(Boolean).join(' ');
}

export function studentLabel(student: StudentDetails) {
  return `${studentName(student)} (SID ${student.sid})`;
}

export async function setStudent(
  assignmentId: string,
  submissionId: number,
  student: Pick<Submission, 'student_name' | 'student_id'>
) {
  return readData(
    await client.PUT(
      '/api/assignments/{assignment_id}/submissions/{submission_id}/student',
      {
        params: {
          path: { assignment_id: assignmentId, submission_id: submissionId },
        },
        body: student,
      }
    )
  );
}

export async function matchRoster(assignmentId: string) {
  return readData(
    await client.POST('/api/assignments/{assignment_id}/submissions/match', {
      params: { path: { assignment_id: assignmentId } },
    })
  );
}
