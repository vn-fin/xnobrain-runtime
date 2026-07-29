import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { Workbook } from '@fortune-sheet/react';
import type { Sheet } from '@fortune-sheet/core';
import LuckyExcel from 'luckyexcel';
import '@fortune-sheet/react/dist/index.css';

const MAX_WORKBOOK_BYTES = 25 * 1024 * 1024;
const IMPORT_TIMEOUT_MS = 30_000;

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

export default function SpreadsheetViewer({ sourceUrl, title }: { sourceUrl: string; title: string }) {
  const [sheets, setSheets] = useState<Sheet[]>();
  const [error, setError] = useState('');
  const viewerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setSheets(undefined);
    setError('');

    void fetch(sourceUrl, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Unable to load workbook (${response.status}).`);
        const blob = await response.blob();
        return importXlsx(new File([blob], title, { type: blob.type }));
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
        <span>Opening workbook…</span>
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
