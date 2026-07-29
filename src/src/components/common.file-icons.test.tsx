import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TreeIcon } from './common';
import type { WorkspaceEntry } from '../types';

const entry = (name: string, language: WorkspaceEntry['language']): WorkspaceEntry => ({
  name,
  path: name,
  type: 'file',
  level: 0,
  language,
  size: '1',
  modified: '',
});

describe('TreeIcon file types', () => {
  it.each([
    ['report.docx', 'document', 'document', 'W'],
    ['budget.xlsx', 'spreadsheet', 'spreadsheet', 'X'],
    ['locations.csv', 'spreadsheet', 'spreadsheet', 'csv'],
    ['slides.pptx', 'presentation', 'presentation', 'P'],
    ['page.html', 'html', 'html', '<>'],
    ['report.pdf', 'pdf', 'pdf', 'pdf'],
  ] as const)('renders a distinct icon for %s', (name, language, className, label) => {
    const { container } = render(<TreeIcon entry={entry(name, language)} />);
    const badge = container.querySelector(`.file-badge.${className}`);
    expect(badge).toBeTruthy();
    expect(badge).toHaveTextContent(label);
  });
});
