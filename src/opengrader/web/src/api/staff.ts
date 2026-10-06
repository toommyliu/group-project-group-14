import { queryOptions } from '@tanstack/react-query';
import { client, readData } from './client';
import type { components } from './schema';

export type Staff = components['schemas']['Staff'];
export type StaffRole = components['schemas']['StaffMember']['role'];

export const staffQuery = queryOptions({
  queryKey: ['staff'],
  queryFn: async ({ signal }) =>
    readData(await client.GET('/api/staff', { signal })),
});

export async function addStaff(email: string, role: StaffRole) {
  return readData(await client.POST('/api/staff', { body: { email, role } }));
}

export async function changeStaffRole(email: string, role: StaffRole) {
  return readData(
    await client.PUT('/api/staff/role', { body: { email, role } })
  );
}

export async function removeStaff(email: string) {
  return readData(await client.POST('/api/staff/remove', { body: { email } }));
}
