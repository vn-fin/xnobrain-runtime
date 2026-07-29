import { describe, expect, it } from 'vitest';
import { detectLanguage } from './workspace';

describe('detectLanguage', () => {
  it.each([
    ['report.docx', 'document'],
    ['budget.xlsx', 'spreadsheet'],
    ['locations.csv', 'spreadsheet'],
    ['slides.pptx', 'presentation'],
    ['dashboard.html', 'html'],
    ['manual.pdf', 'pdf'],
  ] as const)('classifies %s as %s', (path, language) => {
    expect(detectLanguage(path)).toBe(language);
  });
});
