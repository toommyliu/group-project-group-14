import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { authQuery } from '@/api/auth';
import {
  addStaff,
  changeStaffRole,
  removeStaff,
  staffQuery,
} from '@/api/staff';
import { AddStudents } from '@/components/add-students';
import { PageHeading } from '@/components/page-heading';
import { Roster } from '@/components/roster';
import { AddStaff, StaffAccess } from '@/components/staff-access';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useFolderSession } from '@/hooks/use-folder-session';
import { peopleRoute } from '@/router';

type View = 'students' | 'staff';

export default function PeoplePage() {
  const queryClient = useQueryClient();
  const loaded = peopleRoute.useLoaderData();
  const { data: staff } = useQuery({
    ...staffQuery,
    initialData: loaded.staff ?? undefined,
    enabled: loaded.staff !== null,
  });
  const me = useQuery(authQuery).data?.user?.email;
  const [view, setView] = useState<View>('students');
  const [notice, setNotice] = useState('');
  const folderRoster = useFolderSession()?.roster;

  const roster = (
    <Roster initialStudents={loaded.students} onNotice={setNotice} />
  );

  return (
    <>
      <PageHeading
        title="People"
        actions={
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
            <p
              role="status"
              className="text-sm wrap-anywhere text-muted-foreground"
            >
              {notice}
            </p>
            {staff && view === 'staff' ? (
              <AddStaff
                domain={staff.domain}
                onAdd={async (email, role) => {
                  queryClient.setQueryData(
                    staffQuery.queryKey,
                    await addStaff(email, role)
                  );
                }}
                onNotice={setNotice}
              />
            ) : (
              <AddStudents onNotice={setNotice} />
            )}
          </div>
        }
      />
      {folderRoster && (
        <p className="mb-4 text-sm text-muted-foreground">
          {folderRoster.editable ? (
            <>
              This is the roster file in the class folder (
              <code>{folderRoster.files[0]}</code>). Students you add, edit or
              remove here are saved straight into that file, and a submission
              already matched to a student follows a changed name or ID.
            </>
          ) : (
            <>
              This class folder has one roster per section (
              {folderRoster.files.join(', ')}), so its students are shown here
              and edited in those files.
            </>
          )}
          {folderRoster.skipped > 0 &&
            ` ${folderRoster.skipped} row${folderRoster.skipped === 1 ? ' is' : 's are'} not shown: a student needs both a name and an ID.`}
        </p>
      )}
      {staff ? (
        <Tabs
          value={view}
          onValueChange={(next: View) => {
            setView(next);
            setNotice('');
          }}
          className="min-h-0 flex-1 gap-4"
        >
          <div className="border-b">
            <TabsList variant="line">
              <TabsTrigger value="students" className="text-sm">
                Students
              </TabsTrigger>
              <TabsTrigger value="staff" className="text-sm">
                Staff
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent
            value="students"
            keepMounted
            className="flex min-h-0 flex-col"
          >
            {roster}
          </TabsContent>
          <TabsContent value="staff" className="flex min-h-0 flex-col">
            <StaffAccess
              staff={staff}
              me={me}
              onChangeRole={async (email, role) => {
                queryClient.setQueryData(
                  staffQuery.queryKey,
                  await changeStaffRole(email, role)
                );
              }}
              onRemove={async (email) => {
                queryClient.setQueryData(
                  staffQuery.queryKey,
                  await removeStaff(email)
                );
              }}
              onNotice={setNotice}
            />
          </TabsContent>
        </Tabs>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">{roster}</div>
      )}
    </>
  );
}
