import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SpreadsheetViewer, { importXlsx } from './SpreadsheetViewer';

const mocks = vi.hoisted(() => ({
  workbook: vi.fn(),
  transform: vi.fn(),
}));

vi.mock('@fortune-sheet/react', () => ({
  Workbook: (props: Record<string, unknown>) => {
    mocks.workbook(props);
    return <div data-testid="workbook" />;
  },
}));

vi.mock('luckyexcel', () => ({
  default: {
    transformExcelToLucky: mocks.transform,
  },
}));

describe('SpreadsheetViewer', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mocks.workbook.mockReset();
    mocks.transform.mockReset();
  });

  it('imports workbook sheets and disables every editing surface', async () => {
    const sheets = [{ id: 'sheet-1', name: 'Locations', celldata: [] }];
    mocks.transform.mockImplementation((_file, callback) => callback({ sheets }, '{}'));
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob(['xlsx'], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }),
    } as Response);

    render(<SpreadsheetViewer sourceUrl="blob:workbook" title="locations.xlsx" />);

    expect(await screen.findByTestId('workbook')).toBeInTheDocument();
    await waitFor(() => expect(mocks.workbook).toHaveBeenCalled());
    expect(mocks.workbook).toHaveBeenLastCalledWith(expect.objectContaining({
      data: sheets,
      allowEdit: false,
      showToolbar: false,
      showFormulaBar: true,
      showSheetTabs: true,
      cellContextMenu: [],
      headerContextMenu: [],
      sheetTabContextMenu: [],
      hooks: expect.objectContaining({
        beforeUpdateCell: expect.any(Function),
        beforePaste: expect.any(Function),
        beforeAddSheet: expect.any(Function),
        beforeDeleteSheet: expect.any(Function),
        beforeUpdateSheetName: expect.any(Function),
      }),
    }));
    const props = mocks.workbook.mock.lastCall?.[0] as {
      hooks: Record<string, (...args: unknown[]) => boolean>;
    };
    expect(Object.values(props.hooks).every((hook) => hook() === false)).toBe(true);
  });

  it('rejects workbooks above the preview limit', async () => {
    const oversized = new File(
      [new Uint8Array(25 * 1024 * 1024 + 1)],
      'oversized.xlsx',
    );

    await expect(importXlsx(oversized)).rejects.toThrow('maximum 25 MiB');
    expect(mocks.transform).not.toHaveBeenCalled();
  });
});
