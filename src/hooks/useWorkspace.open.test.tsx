import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkspaceEntry } from '../types';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  size: vi.fn(),
  download: vi.fn(),
  read: vi.fn(),
  view: vi.fn(),
  workbook: vi.fn(),
  preview: vi.fn(),
}));

vi.mock('../api/workspace', () => ({
  workspaceApi: {
    ...mocks,
    create: vi.fn(),
    write: vi.fn(),
    upload: vi.fn(),
    remove: vi.fn(),
  },
}));

import { useWorkspace } from './useWorkspace';

const macroWorkbook: WorkspaceEntry = {
  name: 'HPG model - 2026 v3.xlsm',
  path: 'HPG model - 2026 v3.xlsm',
  type: 'file',
  level: 0,
  language: 'spreadsheet',
  size: '1024',
  modified: '',
};

describe('useWorkspace.open', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    Object.values(mocks).forEach((mock) => mock.mockReset());
  });

  it('shows XLSM immediately and streams it directly without normalization', async () => {
    let finish!: (blob: Blob) => void;
    mocks.view.mockReturnValue(new Promise<Blob>((resolve) => { finish = resolve; }));
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:macro-workbook'),
      revokeObjectURL: vi.fn(),
    });
    const { result } = renderHook(() => useWorkspace('big-brother', false));

    let opening!: Promise<void>;
    act(() => {
      opening = result.current.open(macroWorkbook);
    });

    expect(result.current.selected?.name).toBe(macroWorkbook.name);
    expect(result.current.opening).toBe(true);
    expect(mocks.view).toHaveBeenCalledWith(
      'big-brother',
      macroWorkbook.path,
      expect.any(AbortSignal),
    );
    expect(mocks.workbook).not.toHaveBeenCalled();

    await act(async () => {
      finish(new Blob(['PK-workbook'], { type: 'application/vnd.ms-excel.sheet.macroEnabled.12' }));
      await opening;
    });

    expect(result.current.opening).toBe(false);
    expect(result.current.previewUrl).toBe('blob:macro-workbook');
  });

  it('blocks previews larger than 3 MiB without requesting the file', async () => {
    const largeWorkbook = {
      ...macroWorkbook,
      size: String(18_509_277),
    };
    const { result } = renderHook(() => useWorkspace('big-brother', false));

    await act(async () => {
      await result.current.open(largeWorkbook);
    });

    expect(result.current.selected).toEqual(largeWorkbook);
    expect(result.current.previewBlocked).toBe(true);
    expect(result.current.opening).toBe(false);
    expect(mocks.size).not.toHaveBeenCalled();
    expect(mocks.view).not.toHaveBeenCalled();
    expect(mocks.workbook).not.toHaveBeenCalled();
  });

  it('checks unknown file sizes before starting a preview', async () => {
    mocks.size.mockResolvedValue(4 * 1024 * 1024);
    const { result } = renderHook(() => useWorkspace('big-brother', false));

    await act(async () => {
      await result.current.open({ ...macroWorkbook, size: '' });
    });

    expect(mocks.size).toHaveBeenCalledWith(
      'big-brother',
      macroWorkbook.path,
      expect.any(AbortSignal),
    );
    expect(result.current.selected?.size).toBe(String(4 * 1024 * 1024));
    expect(result.current.previewBlocked).toBe(true);
    expect(mocks.view).not.toHaveBeenCalled();
  });
});
