import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { Workbook } from '@fortune-sheet/react';
import type { Sheet } from '@fortune-sheet/core';
import LuckyExcel from 'luckyexcel';
import Papa from 'papaparse';
import '@fortune-sheet/react/dist/index.css';

const MAX_WORKBOOK_BYTES = 25 * 1024 * 1024;
const IMPORT_TIMEOUT_MS = 120_000;

export function importXlsx(file: File): Promise<Sheet[]> {
  if (file.size > MAX_WORKBOOK_BYTES) {
    return Promise.reject(new Error('This workbook is too large to preview (maximum 25 MiB).'));
  }

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(
      () => reject(new Error('The workbook took too long to open.')),
      IMPORT_TIMEOUT_MS,
    );

    try {
      LuckyExcel.transformExcelToLucky(file, (workbook) => {
        window.clearTimeout(timeout);
        if (!Array.isArray(workbook.sheets) || workbook.sheets.length === 0) {
          reject(new Error('The workbook does not contain any visible worksheets.'));
          return;
        }
        resolve(workbook.sheets);
      });
    } catch (cause) {
      window.clearTimeout(timeout);
      reject(cause instanceof Error ? cause : new Error('Unable to parse this workbook.'));
    }
  });
}

export async function importCsv(file: File): Promise<Sheet[]> {
  if (file.size > MAX_WORKBOOK_BYTES) {
    throw new Error('This CSV file is too large to preview (maximum 25 MiB).');
  }
  const parsed = Papa.parse<string[]>(await file.text(), {
    skipEmptyLines: 'greedy',
  });
  if (parsed.errors.length > 0 && parsed.data.length === 0) {
    throw new Error(parsed.errors[0].message || 'Unable to parse this CSV file.');
  }

  const rows = parsed.data;
  const columnCount = rows.reduce((maximum, row) => Math.max(maximum, row.length), 0);
  if (rows.length === 0 || columnCount === 0) {
    throw new Error('The CSV file does not contain any rows.');
  }

  const columnlen: Record<string, number> = {};
  for (let column = 0; column < columnCount; column += 1) {
    const longest = rows.reduce(
      (maximum, row) => Math.max(maximum, String(row[column] ?? '').length),
      0,
    );
    columnlen[String(column)] = Math.min(360, Math.max(72, longest * 7 + 18));
  }

  return [{
    id: 'csv-sheet',
    name: file.name.replace(/\.csv$/i, '') || 'CSV',
    order: 0,
    status: 1,
    row: Math.max(50, rows.length),
    column: Math.max(20, columnCount),
    config: { columnlen },
    celldata: rows.flatMap((row, r) => row.map((value, c) => ({
      r,
      c,
      v: { v: value, m: value, ct: { fa: '@', t: 's' } },
    }))),
  }];
}

export default function SpreadsheetViewer({ sourceUrl, title }: { sourceUrl: string; title: string }) {
  const [sheets, setSheets] = useState<Sheet[]>();
  const [error, setError] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const viewerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setSheets(undefined);
    setError('');
    setElapsed(0);

    void fetch(sourceUrl, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Unable to load workbook (${response.status}).`);
        const blob = await response.blob();
        const file = new File([blob], title, { type: blob.type });
        return title.toLowerCase().endsWith('.csv') ? importCsv(file) : importXlsx(file);
      })
      .then((workbookSheets) => {
        if (!controller.signal.aborted) setSheets(workbookSheets);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Unable to open this workbook.');
        }
      });

    return () => controller.abort();
  }, [sourceUrl, title]);

  useEffect(() => {
    if (sheets || error) return undefined;
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [sheets, error, sourceUrl]);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !sheets) return undefined;
    const disableInputs = () => {
      viewer.querySelectorAll<HTMLElement>('[contenteditable="true"]').forEach((element) => {
        element.setAttribute('contenteditable', 'false');
        element.setAttribute('tabindex', '-1');
      });
    };
    disableInputs();
    const observer = new MutationObserver(disableInputs);
    observer.observe(viewer, { childList: true, subtree: true, attributes: true, attributeFilter: ['contenteditable'] });
    return () => observer.disconnect();
  }, [sheets]);

  if (error) {
    return (
      <div className="gd-sheet-state" role="alert">
        <strong>Spreadsheet preview failed</strong>
        <span>{error}</span>
      </div>
    );
  }

  if (!sheets) {
    return (
      <div className="gd-sheet-state" aria-live="polite">
        <span className="gd-sheet-spinner" />
        <strong>Opening workbook…</strong>
        <span>{elapsed < 10 ? 'Reading worksheets…' : 'Building the spreadsheet view…'}</span>
        <span className="gd-opening-elapsed">{elapsed}s elapsed</span>
      </div>
    );
  }

  const preventWrite = (event: SyntheticEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div
      ref={viewerRef}
      className="gd-spreadsheet"
      aria-label={`Read-only spreadsheet: ${title}`}
      onBeforeInputCapture={preventWrite}
      onPasteCapture={preventWrite}
      onCutCapture={preventWrite}
      onDropCapture={preventWrite}
    >
      <Workbook
        key={sourceUrl}
        data={sheets}
        allowEdit={false}
        showToolbar={false}
        showFormulaBar
        showSheetTabs
        cellContextMenu={[]}
        headerContextMenu={[]}
        sheetTabContextMenu={[]}
        filterContextMenu={[]}
        hooks={{
          beforeUpdateCell: () => false,
          beforePaste: () => false,
          beforeAddSheet: () => false,
          beforeDeleteSheet: () => false,
          beforeUpdateSheetName: () => false,
        }}
        lang="en"
      />
    </div>
  );
}
