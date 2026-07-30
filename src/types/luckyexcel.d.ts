declare module 'luckyexcel' {
  import type { Sheet } from '@fortune-sheet/core';

  export interface LuckyExcelWorkbook {
    info?: {
      name?: string;
      creator?: string;
    };
    sheets: Sheet[];
  }

  const LuckyExcel: {
    transformExcelToLucky(
      file: File,
      callback: (workbook: LuckyExcelWorkbook, serializedWorkbook: string) => void,
    ): void;
  };

  export default LuckyExcel;
}
