import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import api from '@/utils/api';
import { formatDate, formatDateTime } from '@/utils/salesLeads';
import { formatCount, formatPercent } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { DataTable } from '@/components/sales/ReportTables';
import { fill } from '@/components/sales/BatchParts';

export const PERIODS = ['today', 'month', 'year', 'lifetime'];
export const hoursText = (h, language) => fill(ts('pf_hours_value', language), { h: formatCount(h, language) });

const HISTORY_DAYS = 30;

// One employee: the four periods (Data Entry and / or Sales figures), then their permanent day-by-day history and latest work
// sessions. Read-only; every figure is the server's (sales.performance.view).
const EmployeeDialog = ({ user, open, onOpenChange, language }) => {
  const [state, setState] = useState({ perf: null, history: null });      // null = loading, false = failed
  useEffect(() => {
    if (!open || !user) return undefined;
    let cancelled = false;
    setState({ perf: null, history: null });
    api.get(`/sales/performance/employees/${user.id}`).then((res) => { if (!cancelled) setState((s) => ({ ...s, perf: res.data })); })
      .catch(() => { if (!cancelled) setState((s) => ({ ...s, perf: false })); });
    api.get(`/sales/performance/employees/${user.id}/history`, { params: { days: HISTORY_DAYS, sessions: 15 } })
      .then((res) => { if (!cancelled) setState((s) => ({ ...s, history: res.data })); })
      .catch(() => { if (!cancelled) setState((s) => ({ ...s, history: false })); });
    return () => { cancelled = true; };
  }, [open, user]);

  const { perf, history } = state;
  const n = (v) => formatCount(v, language);
  const periodLabel = (p) => ts(`pf_period_${p}`, language);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto" data-testid="employee-dialog">
        <DialogHeader className="text-start sm:text-start pr-8">
          <DialogTitle>{user ? <bdi>{user.name}</bdi> : ts('pf_title', language)}</DialogTitle>
          <DialogDescription>{ts('pf_hours_hint', language)}</DialogDescription>
        </DialogHeader>

        {(perf === null || history === null) && <p className="py-8 text-center text-sm text-gray-500">{ts('loading', language)}</p>}
        {(perf === false || history === false) && <p className="py-8 text-center text-sm text-red-600" role="alert">{ts('bt_load_error', language)}</p>}

        {perf && (
          <div className="space-y-5">
            <h3 className="text-sm font-semibold text-[#0A0A0A]">{ts('pf_detail_periods', language)}</h3>
            {perf.data_entry && (
              <DataTable testid="employee-data-entry" caption={ts('pf_tab_data_entry', language)} rows={PERIODS} rowKey={(p) => p}
                columns={[
                  { key: 'period', label: `${ts('pf_tab_data_entry', language)} - ${ts('pf_period', language)}`, render: (p) => periodLabel(p) },
                  { key: 'entered', label: ts('pf_col_entered', language), numeric: true, render: (p) => n(perf.data_entry[p].leads_entered) },
                  { key: 'hours', label: ts('pf_col_hours', language), numeric: true, render: (p) => hoursText(perf.data_entry[p].work_hours, language) },
                ]} />
            )}
            {perf.sales && (
              <DataTable testid="employee-sales" caption={ts('pf_tab_sales', language)} rows={PERIODS} rowKey={(p) => p}
                columns={[
                  { key: 'period', label: `${ts('pf_tab_sales', language)} - ${ts('pf_period', language)}`, render: (p) => periodLabel(p) },
                  { key: 'worked', label: ts('pf_col_worked', language), numeric: true, render: (p) => n(perf.sales[p].stats.total) },
                  { key: 'ad', label: ts('bt_stat_accepted_direct', language), numeric: true, render: (p) => n(perf.sales[p].stats.accepted_direct) },
                  { key: 'rd', label: ts('bt_stat_rejected_direct', language), numeric: true, render: (p) => n(perf.sales[p].stats.rejected_direct) },
                  { key: 'wl', label: ts('pf_col_wait', language), numeric: true, render: (p) => n(perf.sales[p].stats.wait_listed) },
                  { key: 'wa', label: ts('pf_col_wait_accepted', language), numeric: true, render: (p) => n(perf.sales[p].stats.wait_accepted) },
                  { key: 'wr', label: ts('pf_col_wait_rejected', language), numeric: true, render: (p) => n(perf.sales[p].stats.wait_rejected) },
                  { key: 'ap', label: ts('pf_col_accepted_pct', language), numeric: true, render: (p) => formatPercent(perf.sales[p].stats.pct_accepted, language) },
                  { key: 'rp', label: ts('pf_col_rejected_pct', language), numeric: true, render: (p) => formatPercent(perf.sales[p].stats.pct_rejected, language) },
                  { key: 'cb', label: ts('pf_col_batches', language), numeric: true, render: (p) => n(perf.sales[p].completed_batches) },
                  { key: 'hours', label: ts('pf_col_hours', language), numeric: true, render: (p) => hoursText(perf.sales[p].work_hours, language) },
                ]} />
            )}
          </div>
        )}

        {history && (
          <div className="space-y-5">
            <section>
              <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('pf_detail_history', language)} <span className="font-normal text-gray-500">- {fill(ts('pf_history_days', language), { n: HISTORY_DAYS })}</span></h3>
              {history.days.length === 0
                ? <p className="text-sm text-gray-500" data-testid="history-empty">{ts('pf_no_history', language)}</p>
                : (
                  <DataTable testid="employee-history" caption={ts('pf_detail_history', language)} rows={history.days} rowKey={(d) => d.date}
                    columns={[
                      { key: 'date', label: ts('pf_col_date', language), render: (d) => formatDate(`${d.date}T12:00:00+03:00`, language) },
                      { key: 'entered', label: ts('pf_col_entered', language), numeric: true, render: (d) => n(d.leads_entered) },
                      { key: 'worked', label: ts('pf_col_worked', language), numeric: true, render: (d) => n(d.worked) },
                      { key: 'ad', label: ts('bt_stat_accepted_direct', language), numeric: true, render: (d) => n(d.accepted_direct) },
                      { key: 'rd', label: ts('bt_stat_rejected_direct', language), numeric: true, render: (d) => n(d.rejected_direct) },
                      { key: 'wl', label: ts('pf_col_wait', language), numeric: true, render: (d) => n(d.wait_listed) },
                      { key: 'wa', label: ts('pf_col_wait_accepted', language), numeric: true, render: (d) => n(d.wait_accepted) },
                      { key: 'wr', label: ts('pf_col_wait_rejected', language), numeric: true, render: (d) => n(d.wait_rejected) },
                      { key: 'hours', label: ts('pf_col_hours', language), numeric: true, render: (d) => hoursText(d.work_hours, language) },
                    ]} />
                )}
            </section>
            {history.sessions.length > 0 && (
              <section>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('pf_detail_sessions', language)}</h3>
                <DataTable testid="employee-sessions" caption={ts('pf_detail_sessions', language)} rows={history.sessions} rowKey={(s) => s.started_at}
                  columns={[
                    { key: 'start', label: ts('pf_col_session_start', language), render: (s) => formatDateTime(s.started_at, language) },
                    { key: 'end', label: ts('pf_col_session_end', language), render: (s) => formatDateTime(s.ended_at, language) },
                    { key: 'minutes', label: ts('pf_col_session_minutes', language), numeric: true, render: (s) => n(s.minutes) },
                    { key: 'actions', label: ts('pf_col_session_actions', language), numeric: true, render: (s) => n(s.actions) },
                  ]} />
              </section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default EmployeeDialog;
