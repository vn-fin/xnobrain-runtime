import type { AgentSkill } from '../types';

export const skillCategoryOrder = ['workspace', 'runtime', 'skills', 'sandbox', 'web', 'data', 'code'];

export function groupSkillsByCategory(skills: AgentSkill[]): [string, AgentSkill[]][] {
  const groups = new Map<string, AgentSkill[]>();
  for (const skill of skills) {
    if (!groups.has(skill.category)) groups.set(skill.category, []);
    groups.get(skill.category)!.push(skill);
  }
  return [...groups.entries()].sort((a, b) => {
    const ai = skillCategoryOrder.indexOf(a[0]);
    const bi = skillCategoryOrder.indexOf(b[0]);
    return (ai < 0 ? Number.MAX_SAFE_INTEGER : ai) - (bi < 0 ? Number.MAX_SAFE_INTEGER : bi);
  });
}
