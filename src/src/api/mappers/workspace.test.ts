import { describe, expect, it } from 'vitest';
import { detectLanguage, highlightLanguageForPath } from './workspace';

describe('detectLanguage', () => {
  it.each([
    ['report.docx', 'document'],
    ['budget.xlsx', 'spreadsheet'],
    ['forecast.xlsm', 'spreadsheet'],
    ['locations.csv', 'spreadsheet'],
    ['slides.pptx', 'presentation'],
    ['dashboard.html', 'html'],
    ['manual.pdf', 'pdf'],
    ['app.ts', 'typescript'],
    ['component.tsx', 'typescript'],
    ['deploy.sh', 'shell'],
    ['Dockerfile', 'dockerfile'],
    ['Makefile', 'makefile'],
    ['styles.scss', 'css'],
    ['service.go', 'go'],
    ['compose.yaml', 'yaml'],
    ['Dockerfile.dev', 'dockerfile'],
    ['Widget.vue', 'xml'],
  ] as const)('classifies %s as %s', (path, language) => {
    expect(detectLanguage(path)).toBe(language);
  });

  it.each([
    ['page.html', 'xml'],
    ['component.tsx', 'typescript'],
    ['deploy.sh', 'bash'],
    ['settings.toml', 'ini'],
  ])('maps %s to the %s syntax grammar', (path, grammar) => {
    expect(highlightLanguageForPath(path)).toBe(grammar);
  });
});
