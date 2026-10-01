import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { authQuery, viewAs } from '@/api/auth';
import { addGrader, peopleQuery, removeGrader } from '@/api/people';
import { PeopleManager } from '@/components/people-manager';
import { peopleRoute } from '@/router';

export default function PeoplePage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: people } = useQuery({
    ...peopleQuery,
    initialData: peopleRoute.useLoaderData(),
  });
  const me = useQuery(authQuery).data?.user?.email;
  return (
    <PeopleManager
      people={people}
      me={me}
      onAdd={async (email) => {
        queryClient.setQueryData(peopleQuery.queryKey, await addGrader(email));
      }}
      onRemove={async (email) => {
        queryClient.setQueryData(
          peopleQuery.queryKey,
          await removeGrader(email)
        );
      }}
      onViewAsGrader={async () => {
        await viewAs('grader');
        await queryClient.invalidateQueries({ queryKey: authQuery.queryKey });
        await navigate({ to: '/assignments' });
      }}
    />
  );
}
