import { describe, expect, it } from 'vitest';
import { htmlPreviewDocument } from './WorkspacePanel';

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
