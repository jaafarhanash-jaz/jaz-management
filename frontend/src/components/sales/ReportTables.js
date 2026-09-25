import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatCount, formatPercent } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { ChevronLeft, ChevronRight } from 'lucide-react';

// Tables and paging shared by the dashboard and the reports. On a narrow screen a wide table scrolls inside its own box (the
// Table component wraps itself in one), so the page never scrolls sideways.

// columns: [{ key, label, render(row), numeric }]. `numeric` columns are right-aligned in English and left-aligned in
// Arabic (text-end), like every figure column in the Sales UI.
export const DataTable = ({ columns, rows, rowKey, testid, footerRow = null, caption }) => (
  <Table data-testid={testid}>
    {caption && <caption className="sr-only">{caption}</caption>}
    <TableHeader>
      <TableRow>
        {columns.map((c) => <TableHead key={c.key} className={c.numeric ? 'text-end whitespace-nowrap' : 'text-start'}>{c.label}</TableHead>)}
      </TableRow>
    </TableHeader>
    <TableBody>
      {rows.map((row, i) => (
        <TableRow key={rowKey(row, i)} data-testid={`${testid}-row-${rowKey(row, i)}`}>
          {columns.map((c) => <TableCell key={c.key} className={c.numeric ? 'text-end tabular-nums' : 'text-start'}>{c.render(row)}</TableCell>)}
        </TableRow>
      ))}
    </TableBody>
    {footerRow && (
      <TableFooter>
        <TableRow data-testid={`${testid}-totals`}>
          {columns.map((c) => <TableCell key={c.key} className={`font-semibold ${c.numeric ? 'text-end tabular-nums' : 'text-start'}`}>{footerRow(c)}</TableCell>)}
        </TableRow>
      </TableFooter>
    )}
  </Table>
);

// One row per employee: leads, won, lost, conversion and the four kinds of work. A kind the caller may not see arrives as null
// in every row, and its column is left out (unknown is not zero).
export const PerformanceTable = ({ rows, totals = null, language, testid }) => {
  const kinds = ['calls', 'followups', 'demos', 'trials'].filter((k) => rows.some((r) => r[k] !== null && r[k] !== undefined));
  const columns = [
    { key: 'employee', label: ts('rc_employee', language), render: (r) => (r.employee ? r.employee.name : <span className="text-gray-500">{ts('rc_unassigned_row', language)}</span>) },
    { key: 'leads', label: ts('rc_leads', language), numeric: true, render: (r) => formatCount(r.leads, language) },
    { key: 'won', label: ts('rc_won', language), numeric: true, render: (r) => formatCount(r.won, language) },
    { key: 'lost', label: ts('rc_lost', language), numeric: true, render: (r) => formatCount(r.lost, language) },
    { key: 'conversion', label: ts('rc_conversion', language), numeric: true, render: (r) => formatPercent(r.conversion_rate, language) },
    ...kinds.map((k) => ({ key: k, label: ts(`rc_${k}`, language), numeric: true, render: (r) => formatCount(r[k], language) })),
    { key: 'activities', label: ts('rc_activities', language), numeric: true, render: (r) => formatCount(r.activities, language) },
  ];
  const footer = totals && ((c) => {
    if (c.key === 'employee') return ts('rep_totals', language);
    if (c.key === 'conversion') return formatPercent(totals.conversion_rate, language);
    return formatCount(totals[c.key], language);
  });
  return <DataTable columns={columns} rows={rows} rowKey={(r) => (r.employee ? r.employee.id : 'none')} testid={testid} footerRow={footer} caption={ts('sec_performance', language)} />;
};

// Previous / Next with "page X of Y", in the same markup as the Phase 3-4 lists.
export const Pager = ({ total, limit, offset, onOffset, language, testid, busy = false }) => {
  const pages = Math.max(1, Math.ceil(total / limit));
  const page = Math.floor(offset / limit) + 1;
  if (pages <= 1) return null;
  return (
    <nav className="flex items-center justify-between gap-3 mt-4" aria-label={ts('pagination', language)} data-testid={`${testid}-pagination`}>
      <Button variant="outline" className="rounded-sm" disabled={page <= 1 || busy} onClick={() => onOffset(Math.max(0, offset - limit))} data-testid="page-prev">
        <ChevronLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_prev', language)}
      </Button>
      <span className="text-sm text-gray-600">{ts('pagination_page', language).replace('{page}', page).replace('{pages}', pages)}</span>
      <Button variant="outline" className="rounded-sm" disabled={page >= pages || busy} onClick={() => onOffset(offset + limit)} data-testid="page-next">
        {ts('action_next', language)}<ChevronRight className="w-4 h-4 ms-1 rtl:rotate-180" aria-hidden="true" />
      </Button>
    </nav>
  );
};
