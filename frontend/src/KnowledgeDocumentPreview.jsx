import { FileText, Table } from "@phosphor-icons/react";

const sheetMarker = /^\[sheet:(.+)]$/;

export function isSpreadsheetPreview(preview) {
  return preview?.extraction_method === "xlsx_cells"
    || preview?.mime_type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    || preview?.filename?.toLowerCase().endsWith(".xlsx");
}

export function parseSpreadsheetPreview(text = "") {
  const sheets = [];
  let current = null;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const marker = line.match(sheetMarker);
    if (marker) {
      current = { name: marker[1].trim() || `Sheet ${sheets.length + 1}`, rows: [] };
      sheets.push(current);
      continue;
    }
    if (!current) {
      current = { name: "Sheet 1", rows: [] };
      sheets.push(current);
    }
    current.rows.push(line.split(/\s+\|\s+/).map((cell) => cell.trim()));
  }
  return sheets.filter((sheet) => sheet.rows.length);
}

export function previewSummary(preview) {
  if (!preview?.available || !preview.text) return "该版本尚未提取到可预览内容。";
  if (isSpreadsheetPreview(preview)) {
    const sheets = parseSpreadsheetPreview(preview.text);
    const rowCount = sheets.reduce((total, sheet) => total + sheet.rows.length, 0);
    return `已提取 ${sheets.length || preview.page_or_sheet_count || 0} 个工作表，共 ${rowCount} 行可预览数据。`;
  }
  const text = preview.text
    .replace(/^\[(?:page:\d+|sheet:[^\]]+)]\s*$/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  return text ? `${text.slice(0, 140)}${text.length > 140 ? "…" : ""}` : "该版本尚未提取到可预览内容。";
}

function SpreadsheetTable({ sheet, compact }) {
  const visibleRows = compact ? sheet.rows.slice(0, 6) : sheet.rows;
  const columnCount = Math.max(1, ...visibleRows.map((row) => row.length));
  const [header = [], ...body] = visibleRows;
  return <div className={`kb-sheet-table ${compact ? "compact" : ""}`}>
    <table>
      <thead>
        <tr>{Array.from({ length: columnCount }, (_, index) => (
          <th key={`${index}-${header[index] || ""}`}>{header[index] || `列 ${index + 1}`}</th>
        ))}</tr>
      </thead>
      <tbody>
        {body.map((row, rowIndex) => <tr key={rowIndex}>
          {Array.from({ length: columnCount }, (_, columnIndex) => (
            <td key={columnIndex}>{row[columnIndex] || "—"}</td>
          ))}
        </tr>)}
      </tbody>
    </table>
    {compact && sheet.rows.length > visibleRows.length && <small>仅展示前 {visibleRows.length} 行</small>}
  </div>;
}

export function DocumentPreviewContent({
  preview,
  activeSheet = "",
  onSheetChange = () => undefined,
  compact = false,
}) {
  if (!preview?.available || !preview.text) {
    return <div className="kb-preview-empty">
      <FileText />
      <strong>暂无可预览内容</strong>
      <small>索引完成后可查看解析结果</small>
    </div>;
  }
  if (!isSpreadsheetPreview(preview)) {
    const text = compact
      ? preview.text.split(/\r?\n/).filter(Boolean).slice(0, 10).join("\n")
      : preview.text;
    return <div className="kb-text-preview">
      <pre>{text}</pre>
      {compact && preview.text !== text && <small>仅展示前 10 行</small>}
      {!compact && preview.truncated && <small>预览内容已截断</small>}
    </div>;
  }

  const sheets = parseSpreadsheetPreview(preview.text);
  if (!sheets.length) {
    return <div className="kb-preview-empty"><Table /><strong>未识别到表格数据</strong></div>;
  }
  const selected = sheets.find((sheet) => sheet.name === activeSheet) || sheets[0];
  return <div className="kb-spreadsheet-preview">
    {!compact && sheets.length > 1 && <nav className="kb-sheet-tabs" aria-label="工作表">
      {sheets.map((sheet) => <button
        type="button"
        key={sheet.name}
        className={sheet.name === selected.name ? "active" : ""}
        onClick={() => onSheetChange(sheet.name)}
      >
        {sheet.name}
        <span>{sheet.rows.length}</span>
      </button>)}
    </nav>}
    {compact && <div className="kb-sheet-caption"><Table />{selected.name}</div>}
    <SpreadsheetTable sheet={selected} compact={compact} />
  </div>;
}
