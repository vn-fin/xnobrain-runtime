import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { htmlPreviewDocument, LargeFileNotice, OpeningFile, workspaceNameError } from './WorkspacePanel';
import { detectLanguage } from '../api/mappers/workspace';

describe('workspace HTML preview', () => {
  it('removes active content and injects a restrictive content policy', () => {
    const preview = htmlPreviewDocument(`
      <h1 onclick="steal()">Report</h1>
      <img src="https://tracker.example/pixel.png" srcset="https://tracker.example/2x.png">
      <script>steal()</script>
      <iframe src="https://tracker.example"></iframe>
    `);

    expect(preview).toContain('Report');
    expect(preview).toContain("default-src 'none'");
    expect(preview).not.toContain('onclick');
    expect(preview).not.toContain('<script');
    expect(preview).not.toContain('<iframe');
    expect(preview).not.toContain('srcset');
  });
});

describe('workspace opening state', () => {
  it('rejects traversal and encoded path-like create names', () => {
    expect(workspaceNameError('../escape')).toMatch(/name only/);
    expect(workspaceNameError('%2e%2e')).toMatch(/Encoded/);
    expect(workspaceNameError('safe-notes.md')).toBeUndefined();
  });
  it('routes unknown file formats to the safe download fallback', () => {
    expect(detectLanguage('qa-2026-08-14/qa-unsupported.xyz')).toBe('binary');
    expect(detectLanguage('Dockerfile')).toBe('dockerfile');
    expect(detectLanguage('LICENSE')).toBe('text');
  });

  it('immediately names the file and offers cancellation', () => {
    const cancel = vi.fn();
    render(<OpeningFile name="HPG model - 2026 v3.xlsm" onCancel={cancel} />);

    expect(screen.getByText('Opening HPG model - 2026 v3.xlsm')).toBeInTheDocument();
    expect(screen.getByText('Loading file…')).toBeInTheDocument();
    expect(screen.getByText('0s elapsed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('offers download instead of preview for a large file', () => {
    const download = vi.fn();
    render(
      <LargeFileNotice
        name="HPG model - 2026 v3.xlsm"
        size={String(18_509_277)}
        downloading={false}
        error=""
        onDownload={download}
      />,
    );

    expect(screen.getByText('This file is too large to preview')).toBeInTheDocument();
    expect(screen.getByText(/Preview is limited to 3\.0 MB/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download file' }));
    expect(download).toHaveBeenCalledOnce();
  });
});
