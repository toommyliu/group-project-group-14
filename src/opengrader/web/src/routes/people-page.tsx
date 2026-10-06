import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { authQuery } from '@/api/auth';
import { addGrader, peopleQuery, removeGrader } from '@/api/people';
import { AddStudents } from '@/components/add-students';
import { PageHeading } from '@/components/page-heading';
import { Roster } from '@/components/roster';
import { AddGrader, StaffAccess } from '@/components/staff-access';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { peopleRoute } from '@/router';

type View = 'students' | 'staff';

export default function PeoplePage() {
  const queryClient = useQueryClient();
  const loaded = peopleRoute.useLoaderData();
  const { data: staff } = useQuery({
    ...peopleQuery,
    initialData: loaded.staff ?? undefined,
    enabled: loaded.staff !== null,
  });
  const me = useQuery(authQuery).data?.user?.email;
  const [view, setView] = useState<View>('students');
  const [notice, setNotice] = useState('');

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
              <AddGrader
                domain={staff.domain}
                onAdd={async (email) => {
                  queryClient.setQueryData(
                    peopleQuery.queryKey,
                    await addGrader(email)
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
              onRemove={async (email) => {
                queryClient.setQueryData(
                  peopleQuery.queryKey,
                  await removeGrader(email)
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
