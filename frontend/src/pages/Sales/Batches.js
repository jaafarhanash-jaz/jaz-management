import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import api from '@/utils/api';
import { formatDateTime } from '@/utils/salesLeads';
import { formatCount } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { DataTable, Pager } from '@/components/sales/ReportTables';
import { AttemptsList, BatchStatus, StatsLegend, StatsLine, fill } from '@/components/sales/BatchParts';
import { DataBatchDialog, MasterBatchDialog, TraceDialog, WorkBatchDialog } from '@/components/sales/BatchDialogs';
import { Search } from 'lucide-react';

// The Sales Manager's Batches page: Data Batches (every 100 leads Data Entry entered), Master Batches (every 10 full Data Batches),
// Sales Work Batches (100 leads a salesperson worked, with their accepted / rejected / wait-list split and reassignment) and the
// global search that traces a lead through all of them. Every number comes from the server; the tab is in the URL.
const PAGE = 25;
const ALL = '__all__';
const TABS = ['data', 'master', 'work', 'search'];

const Overview = ({ data, language }) => {
  const n = (v) => formatCount(v, language);
  const open = data.open_data_batch;
  const cells = [
    { key: 'open', label: ts('bt_ov_open_data', language), value: open ? fill(ts('bt_of', language), { n: n(open.lead_count), total: n(data.data_batch_size) }) : ts('bt_ov_none_open', language), progress: open ? open.lead_count / data.data_batch_size : 0 },
    { key: 'full', label: ts('bt_ov_full', language), value: n(data.full_data_batches) },
    { key: 'toward', label: ts('bt_ov_toward_master', language), value: fill(ts('bt_of', language), { n: n(data.data_batches_toward_next_master), total: n(data.master_batch_size) }), progress: data.data_batches_toward_next_master / data.master_batch_size },
    { key: 'masters', label: ts('bt_ov_masters', language), value: n(data.master_batches) },
    { key: 'work-open', label: ts('bt_ov_work_open', language), value: n(data.work_batches_open) },
    { key: 'work-closed', label: ts('bt_ov_work_closed', language), value: n(data.work_batches_closed) },
    { key: 'wait', label: ts('bt_ov_wait', language), value: n(data.wait_list_pending) },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="batches-overview">
      {cells.map((c) => (
        <Card key={c.key} className="p-3 bg-white border border-gray-200 rounded-md" data-testid={`ov-${c.key}`}>
          <p className="text-xs text-gray-500">{c.label}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-[#0A0A0A]">{c.value}</p>
          {c.progress !== undefined && (
            <div className="mt-2 h-1.5 rounded-full bg-gray-100" aria-hidden="true"><div className="h-1.5 rounded-full bg-[#0033A0]" style={{ width: `${Math.round(c.progress * 100)}%` }} /></div>
          )}
        </Card>
      ))}
    </div>
  );
};

// One paginated list of a batch kind. `path` is the API listing; `filters` are extra query parameters.
const useList = (path, filters, offset, refreshKey = 0) => {
  const [state, setState] = useState({ loading: true, error: false, data: null });
  const latest = useRef(0);                     // the newest request wins: a slower, older answer never overwrites the current filter / page
  const key = JSON.stringify(filters);
  const load = useCallback(async () => {
    const id = ++latest.current;
    setState((s) => ({ ...s, loading: true, error: false }));
    try {
      const res = await api.get(path, { params: { ...filters, limit: PAGE, offset } });
      if (id === latest.current) setState({ loading: false, error: false, data: res.data });
    } catch (e) {
      if (id === latest.current) setState({ loading: false, error: true, data: null });
    }
  }, [path, key, offset, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  return [state, load];
};

const ListShell = ({ state, retry, language, empty, testid, children }) => {
  if (state.error) {
    return (
      <div className="py-8 text-center" data-testid={`${testid}-error`}>
        <p className="text-gray-600 mb-3">{ts('bt_load_error', language)}</p>
        <Button variant="outline" className="rounded-sm" onClick={retry}>{ts('action_retry', language)}</Button>
      </div>
    );
  }
  if (!state.data) return <p className="py-8 text-center text-gray-500">{ts('loading', language)}</p>;
  if (state.data.items.length === 0) return <p className="py-10 text-center text-sm text-gray-500" data-testid={`${testid}-empty`}>{empty}</p>;
  return children;
};

const OpenButton = ({ onClick, language, testid }) => (
  <Button variant="outline" size="sm" className="rounded-sm" onClick={onClick} data-testid={testid}>{ts('bt_open_detail', language)}</Button>
);

const DataTab = ({ language, open }) => {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState('');
  const [state, load] = useList('/sales/batches/data', status ? { status } : {}, offset);
  return (
    <div className="space-y-3">
      <div className="min-w-[10rem] max-w-[14rem]">
        <Label htmlFor="data-status" className="text-xs text-gray-600">{ts('bt_filter_status', language)}</Label>
        <Select value={status || ALL} onValueChange={(v) => { setStatus(v === ALL ? '' : v); setOffset(0); }}>
          <SelectTrigger id="data-status" className="mt-1 h-10" data-testid="data-status-filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{ts('bt_filter_all', language)}</SelectItem>
            <SelectItem value="open">{ts('bt_status_open', language)}</SelectItem>
            <SelectItem value="full">{ts('bt_status_full', language)}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <ListShell state={state} retry={load} language={language} empty={ts('bt_empty_data', language)} testid="data-batches">
        {state.data && (
          <>
            <DataTable
              testid="data-batches"
              rows={state.data.items}
              rowKey={(b) => b.id}
              caption={ts('bt_tab_data', language)}
              columns={[
                { key: 'seq', label: ts('bt_col_number', language), render: (b) => <b>#{b.seq}</b> },
                { key: 'status', label: ts('bt_col_status', language), render: (b) => <BatchStatus status={b.status} language={language} /> },
                { key: 'leads', label: ts('bt_col_leads', language), numeric: true, render: (b) => `${formatCount(b.lead_count, language)} / ${formatCount(b.capacity, language)}` },
                { key: 'opened', label: ts('bt_col_opened', language), render: (b) => formatDateTime(b.opened_at, language) },
                { key: 'filled', label: ts('bt_col_filled', language), render: (b) => formatDateTime(b.filled_at, language) },
                { key: 'master', label: ts('bt_col_master', language), render: (b) => (b.master_batch ? `#${b.master_batch.seq}` : '-') },
                { key: 'open', label: '', render: (b) => <OpenButton onClick={() => open(b.id)} language={language} testid={`open-data-${b.seq}`} /> },
              ]}
            />
            <Pager total={state.data.total} limit={PAGE} offset={offset} onOffset={setOffset} language={language} testid="data-batches" />
          </>
        )}
      </ListShell>
    </div>
  );
};

const MasterTab = ({ language, open }) => {
  const [offset, setOffset] = useState(0);
  const [state, load] = useList('/sales/batches/master', {}, offset);
  return (
    <ListShell state={state} retry={load} language={language} empty={ts('bt_empty_master', language)} testid="master-batches">
      {state.data && (
        <>
          <DataTable
            testid="master-batches"
            rows={state.data.items}
            rowKey={(b) => b.id}
            caption={ts('bt_tab_master', language)}
            columns={[
              { key: 'seq', label: ts('bt_col_number', language), render: (b) => <b>#{b.seq}</b> },
              { key: 'formed', label: ts('bt_col_formed', language), render: (b) => formatDateTime(b.formed_at, language) },
              { key: 'batches', label: ts('bt_col_data_batches', language), numeric: true, render: (b) => formatCount(b.data_batch_count, language) },
              { key: 'leads', label: ts('bt_col_leads', language), numeric: true, render: (b) => formatCount(b.lead_count, language) },
              { key: 'open', label: '', render: (b) => <OpenButton onClick={() => open(b.id)} language={language} testid={`open-master-${b.seq}`} /> },
            ]}
          />
          <Pager total={state.data.total} limit={PAGE} offset={offset} onOffset={setOffset} language={language} testid="master-batches" />
        </>
      )}
    </ListShell>
  );
};

const WorkTab = ({ language, open, refreshKey }) => {
  const ref = useSalesReference({ sources: false, assignees: true });
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState('');
  const [employee, setEmployee] = useState('');
  const filters = { ...(status ? { status } : {}), ...(employee ? { employee_id: employee } : {}) };
  const [state, load] = useList('/sales/batches/work', filters, offset, refreshKey);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-[10rem]">
          <Label htmlFor="work-status" className="text-xs text-gray-600">{ts('bt_filter_status', language)}</Label>
          <Select value={status || ALL} onValueChange={(v) => { setStatus(v === ALL ? '' : v); setOffset(0); }}>
            <SelectTrigger id="work-status" className="mt-1 h-10" data-testid="work-status-filter"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{ts('bt_filter_all', language)}</SelectItem>
              <SelectItem value="open">{ts('bt_status_open', language)}</SelectItem>
              <SelectItem value="closed">{ts('bt_status_closed', language)}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-[12rem]">
          <Label htmlFor="work-employee" className="text-xs text-gray-600">{ts('bt_filter_employee', language)}</Label>
          <Select value={employee || ALL} onValueChange={(v) => { setEmployee(v === ALL ? '' : v); setOffset(0); }}>
            <SelectTrigger id="work-employee" className="mt-1 h-10" data-testid="work-employee-filter"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{ts('bt_filter_all', language)}</SelectItem>
              {ref.assignees.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      <ListShell state={state} retry={load} language={language} empty={ts('bt_empty_work', language)} testid="work-batches">
        {state.data && (
          <>
            <DataTable
              testid="work-batches"
              rows={state.data.items}
              rowKey={(b) => b.id}
              caption={ts('bt_tab_work', language)}
              columns={[
                { key: 'seq', label: ts('bt_col_number', language), render: (b) => <b>#{b.seq}</b> },
                { key: 'status', label: ts('bt_col_status', language), render: (b) => <BatchStatus status={b.status} language={language} /> },
                { key: 'original', label: ts('bt_col_original', language), render: (b) => <bdi>{b.original_employee.name}</bdi> },
                { key: 'current', label: ts('bt_col_current', language), render: (b) => <bdi>{b.current_employee.name}</bdi> },
                { key: 'attempts', label: ts('bt_col_attempts', language), numeric: true, render: (b) => formatCount(b.attempt_count, language) },
                { key: 'leads', label: ts('bt_col_leads', language), numeric: true, render: (b) => formatCount(b.lead_count, language) },
                { key: 'split', label: ts('bt_col_split', language), render: (b) => <StatsLine stats={b.stats} language={language} /> },
                { key: 'created', label: ts('bt_col_created', language), render: (b) => formatDateTime(b.created_at, language) },
                { key: 'open', label: '', render: (b) => <OpenButton onClick={() => open(b.id)} language={language} testid={`open-work-${b.seq}`} /> },
              ]}
            />
            <div className="mt-2"><StatsLegend language={language} /></div>
            <Pager total={state.data.total} limit={PAGE} offset={offset} onOffset={setOffset} language={language} testid="work-batches" />
          </>
        )}
      </ListShell>
    </div>
  );
};

const SearchTab = ({ language, openTrace }) => {
  const [text, setText] = useState('');
  const [field, setField] = useState('any');
  const [state, setState] = useState({ status: 'idle', data: null, query: '' });
  const tooShort = text.trim().length < 2;

  const run = async (event) => {
    event.preventDefault();
    if (tooShort) return;
    const query = text.trim();
    setState({ status: 'loading', data: null, query });
    try {
      const res = await api.get('/sales/batches/search', { params: { q: query, field, limit: 25 } });
      setState({ status: 'done', data: res.data, query });
    } catch (e) {
      setState({ status: 'error', data: null, query });
    }
  };

  return (
    <div className="space-y-4">
      <form onSubmit={run} className="flex flex-wrap items-end gap-3" role="search" aria-label={ts('bt_search_label', language)}>
        <div className="flex-1 min-w-[16rem]">
          <Label htmlFor="batch-search" className="text-xs text-gray-600">{ts('bt_search_label', language)}</Label>
          <Input id="batch-search" className="mt-1 h-10" value={text} onChange={(e) => setText(e.target.value)} maxLength={100}
            placeholder={ts('bt_search_placeholder', language)} data-testid="batch-search-input" />
        </div>
        <div className="min-w-[10rem]">
          <Label htmlFor="batch-search-field" className="text-xs text-gray-600">{ts('bt_search_field', language)}</Label>
          <Select value={field} onValueChange={setField}>
            <SelectTrigger id="batch-search-field" className="mt-1 h-10" data-testid="batch-search-field"><SelectValue /></SelectTrigger>
            <SelectContent>
              {['any', 'business_name', 'contact_name', 'phone', 'business_type'].map((f) => <SelectItem key={f} value={f}>{ts(`bt_field_${f}`, language)}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Button type="submit" className="rounded-sm h-10 bg-[#0033A0] hover:bg-[#002277]" disabled={tooShort || state.status === 'loading'} data-testid="batch-search-submit">
          <Search className="w-4 h-4 me-2" aria-hidden="true" />{ts('bt_search_submit', language)}
        </Button>
      </form>
      {tooShort && text.length > 0 && <p className="text-xs text-gray-500">{ts('bt_search_min', language)}</p>}

      {state.status === 'loading' && <p className="text-center text-gray-500">{ts('loading', language)}</p>}
      {state.status === 'error' && <p className="text-center text-red-600" role="alert" data-testid="batch-search-error">{ts('bt_load_error', language)}</p>}
      {state.status === 'done' && state.data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500" data-testid="batch-search-none">{ts('bt_search_none', language)}</p>}
      {state.status === 'done' && state.data.items.length > 0 && (
        <div className="space-y-3" data-testid="batch-search-results">
          <p className="text-xs text-gray-500" data-testid="batch-search-count">
            {fill(ts('bt_search_count', language), { shown: formatCount(state.data.items.length, language), total: formatCount(state.data.total, language) })}
            {state.data.total > state.data.items.length && <> · {ts('bt_search_more', language)}</>}
          </p>
          {state.data.items.map((lead) => (
            <Card key={lead.id} className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`search-result-${lead.id}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold text-[#0A0A0A] break-words"><bdi>{lead.business_name}</bdi></p>
                  <p className="text-sm text-gray-600 break-words">
                    {[lead.business_type, lead.contact_name].filter(Boolean).map((v, i) => <span key={v}>{i > 0 && ' · '}<bdi>{v}</bdi></span>)}
                    {lead.phone && <> · <bdi dir="ltr">{lead.phone}</bdi></>}
                  </p>
                </div>
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => openTrace(lead.id)} data-testid={`trace-${lead.id}`}>{ts('bt_trace', language)}</Button>
              </div>
              <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
                <span>{ts('bt_trace_data_batch', language)}: <b>{lead.data_batch ? `#${lead.data_batch.seq}` : '-'}</b></span>
                <span>{ts('bt_trace_master_batch', language)}: <b>{lead.master_batch ? `#${lead.master_batch.seq}` : '-'}</b></span>
                <span>{ts('bt_trace_work_batches', language)}: <b>{lead.work_batches.length ? lead.work_batches.map((b) => `#${b.seq}`).join(', ') : '-'}</b></span>
                <span>{ts('bt_entered_by', language)}: <bdi className="font-medium">{lead.entered_by.name}</bdi> · {formatDateTime(lead.entered_at, language)}</span>
              </p>
              {lead.attempts.length > 0 && <div className="mt-2"><AttemptsList attempts={lead.attempts} language={language} /></div>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

const SalesBatches = ({ onLogout, language, setLanguage, userRole }) => {
  const { hasModule, loading: accessLoading } = useSalesAccess();
  const [params, setParams] = useSearchParams();
  const tab = TABS.includes(params.get('tab')) ? params.get('tab') : 'data';
  const [overview, setOverview] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dialog, setDialog] = useState({ kind: null, id: null });      // which detail dialog is open (one at a time)

  const loadOverview = useCallback(() => {
    api.get('/sales/batches/overview').then((res) => setOverview(res.data)).catch(() => setOverview(false));
  }, []);
  useEffect(() => { if (!accessLoading && hasModule('batches')) loadOverview(); }, [accessLoading, hasModule, loadOverview]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('batches')) return shell(<SalesNoAccess language={language} />);

  const openDialog = (kind) => (id) => setDialog({ kind, id });
  const close = (kind) => (isOpen) => { if (!isOpen && dialog.kind === kind) setDialog({ kind: null, id: null }); };
  const setTab = (next) => setParams(next === 'data' ? {} : { tab: next }, { replace: true });
  const changed = () => { setRefreshKey((k) => k + 1); loadOverview(); };

  return shell(
    <div className="space-y-5">
      <div>
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-batches-title">{ts('bt_title', language)}</h1>
        <p className="text-sm text-gray-500 mt-1">{ts('bt_subtitle', language)}</p>
      </div>

      {overview && <Overview data={overview} language={language} />}
      {overview === false && <p className="text-sm text-red-600" role="alert">{ts('bt_load_error', language)}</p>}

      <Card className="p-4 bg-white border border-gray-200 rounded-md">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="h-auto flex-wrap justify-start gap-1" data-testid="batches-tabs">
            {TABS.map((t) => <TabsTrigger key={t} value={t} data-testid={`tab-${t}`}>{ts(`bt_tab_${t}`, language)}</TabsTrigger>)}
          </TabsList>
          <TabsContent value="data" className="mt-4"><DataTab language={language} open={openDialog('data')} /></TabsContent>
          <TabsContent value="master" className="mt-4"><MasterTab language={language} open={openDialog('master')} /></TabsContent>
          <TabsContent value="work" className="mt-4"><WorkTab language={language} open={openDialog('work')} refreshKey={refreshKey} /></TabsContent>
          <TabsContent value="search" className="mt-4"><SearchTab language={language} openTrace={openDialog('trace')} /></TabsContent>
        </Tabs>
      </Card>

      <DataBatchDialog batchId={dialog.kind === 'data' ? dialog.id : null} open={dialog.kind === 'data'} onOpenChange={close('data')} language={language} />
      <MasterBatchDialog batchId={dialog.kind === 'master' ? dialog.id : null} open={dialog.kind === 'master'} onOpenChange={close('master')} language={language} onOpenData={openDialog('data')} />
      <WorkBatchDialog batchId={dialog.kind === 'work' ? dialog.id : null} open={dialog.kind === 'work'} onOpenChange={close('work')} language={language} onChanged={changed} />
      <TraceDialog leadId={dialog.kind === 'trace' ? dialog.id : null} open={dialog.kind === 'trace'} onOpenChange={close('trace')} language={language}
        onOpenData={openDialog('data')} onOpenMaster={openDialog('master')} onOpenWork={openDialog('work')} />
    </div>,
  );
};

export default SalesBatches;
