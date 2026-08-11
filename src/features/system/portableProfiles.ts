import type { Agent } from '../../types';

export function sortPortableAgents(agents: Agent[]): Agent[] {
  return [...agents].sort((left, right) => {
    if (left.id === 'big-brother') return -1;
    if (right.id === 'big-brother') return 1;
    return left.title.localeCompare(right.title);
  });
}
