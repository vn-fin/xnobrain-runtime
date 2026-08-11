import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CodeViewer, { highlightedSource, lineNumberText } from './CodeViewer';

describe('CodeViewer', () => {
  it('highlights TypeScript and renders editor-style line numbers', () => {
    const content = 'const answer: number = 42;\nconsole.log(answer);';
    const { container } = render(<CodeViewer content={content} path="src/app.ts" />);

    expect(container.querySelector('.gd-code-editor')).toHaveAttribute('data-language', 'typescript');
    expect(container.querySelector('.gd-code-editor')).toHaveAttribute('data-highlighted', 'true');
    expect(container.querySelector('.hljs-keyword')).toHaveTextContent('const');
    expect(container.querySelector('.gd-code-lines')).toHaveTextContent('1 2');
  });

  it('detects an extensionless shell script from its shebang', () => {
    const result = highlightedSource('#!/usr/bin/env bash\necho "ready"', 'deploy');

    expect(result.language).toBe('bash');
    expect(result.highlighted).toBe(true);
    expect(result.html).toContain('hljs-meta');
  });

  it('escapes rather than highlights very large source files', () => {
    const result = highlightedSource(`<script>${'x'.repeat(1_000_000)}</script>`, 'large.ts');

    expect(result.highlighted).toBe(false);
    expect(result.html).toContain('&lt;script&gt;');
    expect(result.html).not.toContain('<script>');
  });

  it('keeps one line number for an empty file', () => {
    expect(lineNumberText('')).toBe('1');
  });
});
