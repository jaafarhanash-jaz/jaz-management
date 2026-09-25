import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { CAMPAIGN_STATUSES, PRIORITIES, STAGES, campaignStatusName, priorityName, stageName } from '@/utils/salesLeads';
import {
  DEFAULT_RANGE, RANGES, REPORTS, REPORT_PAGE_SIZE, customPeriodProblem, formatDay, formatTime, periodErrorText, periodParams,
} from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import PeriodFilter from '@/components/sales/PeriodFilter';
import { Pager } from '@/components/sales/ReportTables';
import {
  ActivitiesView, CampaignsView, ConversionView, EmployeesView, LeadsView, OnboardingView, PipelineView, SourcesView,
} from '@/components/sales/ReportViews';

const ALL = '__all__';
const ONBOARDING_STAGES = ['won', 'assigned', 'contacted', 'setup_started', 'company_configured', 'employees_added', 'training', 'activated'];

const VIEWS = {
  leads: LeadsView, pipeline: PipelineView, employees: EmployeesView, sources: SourcesView, campaigns: CampaignsView,
  conversion: ConversionView, activities: ActivitiesView, onboarding: OnboardingView,
};

// Which URL parameter carries which filter, and which API parameter it becomes. The URL is the state (report, period, filters,
// page), so links, Back and Forward land on exactly the same view.
const FILTER_PARAMS = {
  employee: { url: 'employee', api: 'employee_id' },
  source: { url: 'source', api: 'source' },
  campaign: { url: 'campaign', api: 'campaign_id' },
  stage: { url: 'stage', api: 'pipeline_stage' },
  priority: { url: 'priority', api: 'priority' },
  basis: { url: 'basis', api: 'basis' },
  campaign_status: { url: 'cstatus', api: 'campaign_status' },
  onb_stage: { url: 'ostage', api: 'stage' },
  onb_employee: { url: 'oemployee', api: 'assigned_to' },
};

const FilterSelect = ({ id, label, value, options, onChange, testid, allLabel }) => (
  <div className="min-w-[10rem]">
    <Label htmlFor={id} className="text-xs text-gray-600">{label}</Label>
    <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? '' : v)}>
      <SelectTrigger id={id} className="mt-1 h-10" data-testid={testid}><SelectValue /></SelectTrigger>
      <SelectContent>
        {allLabel !== null && <SelectItem value={ALL}>{allLabel}</SelectItem>}
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  </div>
);

// The Reports page: pick a report, a period and its filters; the server aggregates (and limits it to what the caller may see)
// and this page shows the summary, a chart where one helps, and a table with pages.
const SalesReports = ({ onLogout, language, setLanguage, userRole }) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  const [params, setParams] = useSearchParams();

  const available = REPORTS.filter((r) => r.available(can));
  const current = available.find((r) => r.key === params.get('report')) || available[0];

  const range = RANGES.includes(params.get('range')) ? params.get('range') : DEFAULT_RANGE;
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const filterValue = (name) => params.get(FILTER_PARAMS[name].url) || '';

  const canPickEmployee = can('sales.leads.scope_all') && can('sales.leads.assign');
  const ref = useSalesReference({
    sources: can('sales.leads.view'),
    campaigns: can('sales.campaigns.view') && can('sales.leads.view'),
    assignees: canPickEmployee,
    onboardingAssignees: can('sales.onboarding.assign') && can('sales.onboarding.scope_all'),
  });

  const [state, setState] = useState({ loading: true, data: null, error: null, key: null });    // `key`: which report `data` / `error` belong to
  const requestRef = useRef(0);
  const problem = range === 'custom' ? customPeriodProblem(from, to) : null;
  const problemKey = problem ? problem.key : null;
  const queryKey = params.toString();

  const load = useCallback(async () => {
    if (!current || problemKey) return;
    const id = ++requestRef.current;
    const reportKey = current.key;
    setState((s) => ({ ...s, loading: true, error: null }));
    const query = periodParams({ range, from, to });
    current.filters.forEach((name) => {
      const value = params.get(FILTER_PARAMS[name].url);
      if (value) query[FILTER_PARAMS[name].api] = value;
    });
    if (current.paged) { query.limit = REPORT_PAGE_SIZE; query.offset = (page - 1) * REPORT_PAGE_SIZE; }
    try {
      const res = await api.get(`/sales/reports/${current.path}`, { params: query });
      if (id === requestRef.current) setState({ loading: false, data: res.data, error: null, key: reportKey });
    } catch (e) {
      if (id === requestRef.current) setState({ loading: false, data: null, error: { status: e.response?.status, detail: e.response?.data?.detail }, key: reportKey });
    }
  }, [current && current.key, range, from, to, page, problemKey, queryKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!accessLoading && hasModule('reports')) load(); }, [accessLoading, hasModule, load]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('reports')) return shell(<SalesNoAccess language={language} />);

  const update = (mutate) => {
    const next = new URLSearchParams(params);
    mutate(next);
    setParams(next, { replace: true });
  };
  const setFilter = (name, value) => update((next) => {
    const key = FILTER_PARAMS[name].url;
    if (!value) next.delete(key); else next.set(key, value);
    next.delete('page');
  });
  const setReport = (key) => update((next) => {
    next.set('report', key);
    Object.values(FILTER_PARAMS).forEach((p) => next.delete(p.url));
    next.delete('page');
  });
  const setPeriod = ({ range: r, from: f, to: t }) => update((next) => {
    if (r === DEFAULT_RANGE) next.delete('range'); else next.set('range', r);
    if (r === 'custom') { next.set('from', f); next.set('to', t); } else { next.delete('from'); next.delete('to'); }
    next.delete('page');
  });
  const setOffset = (offset) => update((next) => {
    const p = Math.floor(offset / REPORT_PAGE_SIZE) + 1;
    if (p <= 1) next.delete('page'); else next.set('page', String(p));
  });

  if (!current) {
    return shell(
      <Card className="p-10 text-center bg-white border border-gray-200 text-gray-500" data-testid="reports-none">{ts('rep_none_available', language)}</Card>,
    );
  }

  // what was fetched for ANOTHER report (the user just switched) is not shown under this one
  const { loading } = state;
  const data = state.key === current.key ? state.data : null;
  const error = state.key === current.key ? state.error : null;
  const View = VIEWS[current.key];
  const errorText = error && error.status === 400 && error.detail ? periodErrorText(error.detail, language) : null;
  const wants = (name) => current.filters.includes(name);

  const employeeOptions = ref.assignees.map((a) => ({ value: a.id, label: a.name }));
  const sourceOptions = ref.sources.map((s) => ({ value: s.key, label: language === 'ar' ? s.name_ar : s.name_en }));
  const campaignOptions = ref.campaigns.map((c) => ({ value: c.id, label: c.name }));

  return shell(
    <div className="space-y-5" aria-busy={loading}>
      <div>
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-reports-title">{ts('reports_title', language)}</h1>
        <p className="text-sm text-gray-500 mt-1">{ts('reports_subtitle', language)}</p>
      </div>

      <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <FilterSelect id="rep-select" label={ts('rep_select', language)} value={current.key} testid="report-select" allLabel={null}
            options={available.map((r) => ({ value: r.key, label: ts(r.nameKey, language) }))} onChange={(v) => v && setReport(v)} />
          <PeriodFilter value={{ range, from, to }} onChange={setPeriod} language={language} idPrefix="rep-period" />
        </div>
        <p className="text-xs text-gray-500" data-testid="report-description">{ts(current.descKey, language)}</p>

        {current.filters.length > 0 && (
          <div className="flex flex-wrap items-end gap-4 border-t border-gray-100 pt-4" data-testid="report-filters" aria-label={ts('rep_filters', language)}>
            {wants('employee') && canPickEmployee && (
              <FilterSelect id="rep-employee" label={ts('filter_employee', language)} value={filterValue('employee')} options={employeeOptions} testid="rep-employee"
                allLabel={ts('filter_all_employees', language)} onChange={(v) => setFilter('employee', v)} />
            )}
            {wants('source') && (
              <FilterSelect id="rep-source" label={ts('rep_filter_source', language)} value={filterValue('source')} options={sourceOptions} testid="rep-source"
                allLabel={ts('filter_all', language)} onChange={(v) => setFilter('source', v)} />
            )}
            {wants('campaign') && can('sales.campaigns.view') && (
              <FilterSelect id="rep-campaign" label={ts('rep_filter_campaign', language)} value={filterValue('campaign')} options={campaignOptions} testid="rep-campaign"
                allLabel={ts('filter_all', language)} onChange={(v) => setFilter('campaign', v)} />
            )}
            {wants('stage') && (
              <FilterSelect id="rep-stage" label={ts('rep_filter_stage', language)} value={filterValue('stage')} testid="rep-stage" allLabel={ts('filter_all', language)}
                options={STAGES.map((s) => ({ value: s, label: stageName(s, language) }))} onChange={(v) => setFilter('stage', v)} />
            )}
            {wants('priority') && (
              <FilterSelect id="rep-priority" label={ts('rep_filter_priority', language)} value={filterValue('priority')} testid="rep-priority" allLabel={ts('filter_all', language)}
                options={PRIORITIES.map((p) => ({ value: p, label: priorityName(p, language) }))} onChange={(v) => setFilter('priority', v)} />
            )}
            {wants('basis') && (
              <FilterSelect id="rep-basis" label={ts('rep_filter_basis', language)} value={filterValue('basis') || 'created'} testid="rep-basis" allLabel={null}
                options={[{ value: 'created', label: ts('rep_basis_created', language) }, { value: 'all', label: ts('rep_basis_all', language) }]}
                onChange={(v) => setFilter('basis', v === 'created' ? '' : v)} />
            )}
            {wants('campaign_status') && (
              <FilterSelect id="rep-cstatus" label={ts('rep_filter_campaign_status', language)} value={filterValue('campaign_status')} testid="rep-cstatus" allLabel={ts('filter_all', language)}
                options={CAMPAIGN_STATUSES.map((s) => ({ value: s, label: campaignStatusName(s, language) }))} onChange={(v) => setFilter('campaign_status', v)} />
            )}
            {wants('onb_stage') && (
              <FilterSelect id="rep-ostage" label={ts('rep_filter_stage', language)} value={filterValue('onb_stage')} testid="rep-ostage" allLabel={ts('filter_all', language)}
                options={ONBOARDING_STAGES.map((s) => ({ value: s, label: ts(`onb_stage_${s}`, language) }))} onChange={(v) => setFilter('onb_stage', v)} />
            )}
            {wants('onb_employee') && can('sales.onboarding.assign') && can('sales.onboarding.scope_all') && (
              <FilterSelect id="rep-oemployee" label={ts('rep_filter_onb_employee', language)} value={filterValue('onb_employee')} testid="rep-oemployee" allLabel={ts('filter_all', language)}
                options={ref.onboardingAssignees.map((a) => ({ value: a.id, label: a.name }))} onChange={(v) => setFilter('onb_employee', v)} />
            )}
          </div>
        )}
      </Card>

      {data && (
        <p className="text-xs text-gray-500" data-testid="report-meta">
          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium border bg-blue-50 text-[#0033A0] border-blue-200 me-2" data-testid="report-scope">
            {ts(data.scope === 'team' ? 'rep_scope_team' : 'rep_scope_personal', language)}
          </span>
          {data.period && `${ts('period_showing', language).replace('{from}', formatDay(data.period.date_from, language)).replace('{to}', formatDay(data.period.date_to, language))} · ${ts('period_tz_note', language)} · `}
          {ts('dash_as_of', language).replace('{time}', formatTime(data.as_of, language))}
        </p>
      )}

      {problem && <Card className="p-4 bg-red-50 border border-red-200 text-red-800 text-sm" role="alert" data-testid="report-period-problem">{ts(problem.key, language).replace('{n}', problem.n)}</Card>}
      {errorText && <Card className="p-4 bg-red-50 border border-red-200 text-red-800 text-sm" role="alert" data-testid="report-period-error">{errorText}</Card>}
      {error && error.status === 403 && <SalesNoAccess language={language} />}
      {error && !errorText && error.status !== 403 && (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid="report-error">
          <p className="text-gray-600 mb-4">{ts('rep_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button>
        </Card>
      )}
      {loading && !data && !error && <div className="text-center py-12 text-gray-500" data-testid="report-loading">{ts('loading', language)}</div>}

      {data && !error && (
        <div data-testid={`report-${current.key}`}>
          <View data={data} language={language} sources={ref.sources} />
          {current.paged && <Pager total={data.total} limit={REPORT_PAGE_SIZE} offset={(page - 1) * REPORT_PAGE_SIZE} onOffset={setOffset} language={language} testid="report" busy={loading} />}
        </div>
      )}
    </div>,
  );
};

export default SalesReports;
