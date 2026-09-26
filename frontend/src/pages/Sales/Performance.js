import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import api from '@/utils/api';
import { formatCount, formatPercent } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { DataTable, Pager } from '@/components/sales/ReportTables';
import EmployeeDialog, { PERIODS, hoursText } from '@/components/sales/PerformanceDialogs';

// The Sales Manager's employee performance page: Data Entry (leads entered, work hours) and Sales (leads worked, accepted / rejected
// directly and after the wait list, completed batches, work hours) for today, this month, this year and lifetime. The figures are the
// server's - counted from permanent records (leads, attempts, work sessions) - and the page only lays them out.
const TABS = ['data-entry', 'sales'];
const PAGE = 25;

const PeriodSwitch = ({ value, onChange, language }) => (
  <div className="flex flex-wrap items-center gap-1" role="group" aria-label={ts('pf_period', language)} data-testid="period-switch">
    {PERIODS.map((p) => (
      <Button key={p} type="button" size="sm" variant={value === p ? 'default' : 'outline'} aria-pressed={value === p}
        className={`rounded-sm ${value === p ? 'bg-[#0033A0] hover:bg-[#002277]' : ''}`} onClick={() => onChange(p)} data-testid={`period-${p}`}>
        {ts(`pf_period_${p}`, language)}
      </Button>
    ))}
  </div>
);

const Person = ({ item, language, onOpen }) => (
  <span className="inline-flex flex-wrap items-center gap-2">
    <button type="button" className="text-[#0033A0] hover:underline font-medium text-start" onClick={() => onOpen(item.user)} data-testid={`open-employee-${item.user.id}`}><bdi>{item.user.name}</bdi></button>
    {item.status && item.status !== 'active' && <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600">{ts('pf_inactive', language)}</span>}
  </span>
);

// One page of a performance list (the server paginates and filters by name; the newest request wins).
const useLoad = (path, q, offset) => {
  const [state, setState] = useState({ loading: true, error: false, data: null });
  const latest = useRef(0);
  const load = useCallback(async () => {
    const id = ++latest.current;
    setState((s) => ({ ...s, loading: true, error: false }));
    try {
      const res = await api.get(path, { params: { limit: PAGE, offset, ...(q ? { q } : {}) } });
      if (id === latest.current) setState({ loading: false, error: false, data: res.data });
    } catch (e) {
      if (id === latest.current) setState({ loading: false, error: true, data: null });
    }
  }, [path, q, offset]);
  useEffect(() => { load(); }, [load]);
  return [state, load];
};

const Shell = ({ state, retry, language, empty, children }) => {
  if (state.error) {
    return (
      <div className="py-8 text-center" data-testid="performance-error">
        <p className="text-gray-600 mb-3">{ts('bt_load_error', language)}</p>
        <Button variant="outline" className="rounded-sm" onClick={retry}>{ts('action_retry', language)}</Button>
      </div>
    );
  }
  if (!state.data) return <p className="py-8 text-center text-gray-500">{ts('loading', language)}</p>;
  if (state.data.items.length === 0) return <p className="py-10 text-center text-sm text-gray-500" data-testid="performance-empty">{empty}</p>;
  return children;
};

const DataEntryTab = ({ period, language, onOpen, q, offset, onOffset }) => {
  const [state, retry] = useLoad('/sales/performance/data-entry', q, offset);
  const n = (v) => formatCount(v, language);
  return (
    <Shell state={state} retry={retry} language={language} empty={ts('pf_empty_data_entry', language)}>
      {state.data && (
        <DataTable testid="perf-data-entry" caption={ts('pf_tab_data_entry', language)} rows={state.data.items} rowKey={(i) => i.user.id}
          columns={[
            { key: 'employee', label: ts('pf_col_employee', language), render: (i) => <Person item={i} language={language} onOpen={onOpen} /> },
            { key: 'entered', label: ts('pf_col_entered', language), numeric: true, render: (i) => n(i.periods[period].leads_entered) },
            { key: 'hours', label: ts('pf_col_hours', language), numeric: true, render: (i) => hoursText(i.periods[period].work_hours, language) },
          ]} />
      )}
      {state.data && <Pager total={state.data.total} limit={PAGE} offset={offset} onOffset={onOffset} language={language} testid="perf-data-entry" busy={state.loading} />}
    </Shell>
  );
};

const SalesTab = ({ period, language, onOpen, q, offset, onOffset }) => {
  const [state, retry] = useLoad('/sales/performance/sales', q, offset);
  const n = (v) => formatCount(v, language);
  const s = (i) => i.periods[period].stats;
  return (
    <Shell state={state} retry={retry} language={language} empty={ts('pf_empty_sales', language)}>
      {state.data && (
        <DataTable testid="perf-sales" caption={ts('pf_tab_sales', language)} rows={state.data.items} rowKey={(i) => i.user.id}
          columns={[
            { key: 'employee', label: ts('pf_col_employee', language), render: (i) => <Person item={i} language={language} onOpen={onOpen} /> },
            { key: 'worked', label: ts('pf_col_worked', language), numeric: true, render: (i) => n(s(i).total) },
            { key: 'ad', label: ts('bt_stat_accepted_direct', language), numeric: true, render: (i) => n(s(i).accepted_direct) },
            { key: 'rd', label: ts('bt_stat_rejected_direct', language), numeric: true, render: (i) => n(s(i).rejected_direct) },
            { key: 'wl', label: ts('pf_col_wait', language), numeric: true, render: (i) => n(s(i).wait_listed) },
            { key: 'wp', label: ts('pf_col_wait_pending', language), numeric: true, render: (i) => n(s(i).wait_pending) },
            { key: 'wa', label: ts('pf_col_wait_accepted', language), numeric: true, render: (i) => n(s(i).wait_accepted) },
            { key: 'wr', label: ts('pf_col_wait_rejected', language), numeric: true, render: (i) => n(s(i).wait_rejected) },
            { key: 'ap', label: ts('pf_col_accepted_pct', language), numeric: true, render: (i) => formatPercent(s(i).pct_accepted, language) },
            { key: 'rp', label: ts('pf_col_rejected_pct', language), numeric: true, render: (i) => formatPercent(s(i).pct_rejected, language) },
            { key: 'cb', label: ts('pf_col_batches', language), numeric: true, render: (i) => n(i.periods[period].completed_batches) },
            { key: 'hours', label: ts('pf_col_hours', language), numeric: true, render: (i) => hoursText(i.periods[period].work_hours, language) },
          ]} />
      )}
      {state.data && <Pager total={state.data.total} limit={PAGE} offset={offset} onOffset={onOffset} language={language} testid="perf-sales" busy={state.loading} />}
    </Shell>
  );
};

const SalesPerformance = ({ onLogout, language, setLanguage, userRole }) => {
  const { hasModule, loading: accessLoading } = useSalesAccess();
  const [params, setParams] = useSearchParams();
  const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'data-entry';
  const period = PERIODS.includes(params.get('period')) ? params.get('period') : 'today';
  const offset = Math.max(0, parseInt(params.get('offset') || '0', 10) || 0);
  const q = params.get('q') || '';
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState(q);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('performance')) return shell(<SalesNoAccess language={language} />);

  const update = (mutate) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next, { replace: true });
  };
  const setTab = (t) => update((next) => { if (t === 'data-entry') next.delete('tab'); else next.set('tab', t); next.delete('offset'); });
  const setPeriod = (p) => update((next) => { if (p === 'today') next.delete('period'); else next.set('period', p); });
  const setOffset = (o) => update((next) => { if (o > 0) next.set('offset', String(o)); else next.delete('offset'); });
  const search = (event) => {
    event.preventDefault();
    update((next) => { const v = draft.trim(); if (v) next.set('q', v); else next.delete('q'); next.delete('offset'); });
  };
  const listProps = { period, language, onOpen: setSelected, q, offset, onOffset: setOffset };

  return shell(
    <div className="space-y-5">
      <div>
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-performance-title">{ts('pf_title', language)}</h1>
        <p className="text-sm text-gray-500 mt-1">{ts('pf_subtitle', language)}</p>
      </div>
      <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList data-testid="performance-tabs">
              {TABS.map((t) => <TabsTrigger key={t} value={t} data-testid={`tab-${t}`}>{ts(`pf_tab_${t.replace('-', '_')}`, language)}</TabsTrigger>)}
            </TabsList>
          </Tabs>
          <PeriodSwitch value={period} onChange={setPeriod} language={language} />
        </div>
        <form onSubmit={search} className="flex flex-wrap items-center gap-2" role="search" aria-label={ts('pf_search', language)}>
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={100} className="h-9 max-w-xs" placeholder={ts('pf_search', language)}
            aria-label={ts('pf_search', language)} data-testid="perf-search" />
          <Button type="submit" variant="outline" size="sm" className="rounded-sm" data-testid="perf-search-submit">{ts('bt_search_submit', language)}</Button>
        </form>
        {tab === 'data-entry' ? <DataEntryTab {...listProps} /> : <SalesTab {...listProps} />}
        <p className="text-xs text-gray-500">{ts('pf_hours_hint', language)}</p>
      </Card>
      <EmployeeDialog user={selected} open={selected !== null} onOpenChange={(o) => { if (!o) setSelected(null); }} language={language} />
    </div>,
  );
};

export default SalesPerformance;
