import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { stageName } from '@/utils/salesLeads';
import {
  DEFAULT_RANGE, KPIS, RANGES, basisHint, basisName, customPeriodProblem, formatCount, formatDay, formatMoney, formatPercent, formatTime,
  kpiDefinition, kpiLabel, periodErrorText, periodParams,
} from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import PeriodFilter from '@/components/sales/PeriodFilter';
import { BarList, BasisPill, KpiCard, Section } from '@/components/sales/ReportParts';
import { PerformanceTable } from '@/components/sales/ReportTables';

const ALL = '__all__';

const onbStageName = (stage, language) => ts(`onb_stage_${stage}`, language);

// The Sales dashboard: a period, the key figures, the pipeline, the team's performance, sources, campaigns, activity and
// onboarding. Everything is computed by GET /api/sales/dashboard; a section the caller may not see arrives as null and is
// simply not drawn (never as a zero). The period, and the employee a team caller narrows to, live in the URL.
const SalesDashboard = ({ language }) => {
  const { can } = useSalesAccess();
  const [params, setParams] = useSearchParams();
  const range = RANGES.includes(params.get('range')) ? params.get('range') : DEFAULT_RANGE;
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const employee = params.get('employee') || '';
  const canPickEmployee = can('sales.leads.scope_all') && can('sales.leads.assign');
  const ref = useSalesReference({ sources: false, assignees: canPickEmployee });

  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [showDefinitions, setShowDefinitions] = useState(false);
  const requestRef = useRef(0);

  const problem = range === 'custom' ? customPeriodProblem(from, to) : null;
  const problemKey = problem ? problem.key : null;          // a primitive, so `load` keeps its identity while nothing changed

  const load = useCallback(async () => {
    if (problemKey) return;
    const id = ++requestRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    const query = periodParams({ range, from, to });
    if (employee) query.employee_id = employee;
    try {
      const res = await api.get('/sales/dashboard', { params: query });
      if (id === requestRef.current) setState({ loading: false, data: res.data, error: null });
    } catch (e) {
      if (id === requestRef.current) setState({ loading: false, data: null, error: { status: e.response?.status, detail: e.response?.data?.detail } });
    }
  }, [range, from, to, employee, problemKey]);

  useEffect(() => { load(); }, [load]);

  const setPeriod = ({ range: nextRange, from: nextFrom, to: nextTo }) => {
    const next = new URLSearchParams(params);
    if (nextRange === DEFAULT_RANGE) next.delete('range'); else next.set('range', nextRange);
    if (nextRange === 'custom') { next.set('from', nextFrom); next.set('to', nextTo); } else { next.delete('from'); next.delete('to'); }
    setParams(next, { replace: true });
  };
  const setEmployee = (value) => {
    const next = new URLSearchParams(params);
    if (!value) next.delete('employee'); else next.set('employee', value);
    setParams(next, { replace: true });
  };

  const { data, loading, error } = state;
  const errorText = error && error.status === 400 && error.detail ? periodErrorText(error.detail, language) : null;

  return (
    <div className="space-y-5" data-testid="sales-dashboard" aria-busy={loading}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold text-[#0A0A0A]" data-testid="dashboard-title">{ts('dash_title', language)}</h2>
          <p className="text-sm text-gray-500 mt-1">{ts('dash_subtitle', language)}</p>
        </div>
        {data && (
          <div className="text-sm text-gray-600 space-y-1 sm:text-end" data-testid="dashboard-meta">
            <p>
              <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium border bg-blue-50 text-[#0033A0] border-blue-200" data-testid="dashboard-scope">
                {ts(data.scope === 'team' ? 'dash_scope_team' : 'dash_scope_personal', language)}
              </span>
              {data.employee && <span className="ms-2" data-testid="dashboard-narrowed">{ts('dash_narrowed', language).replace('{name}', data.employee.name)}</span>}
            </p>
            <p className="text-xs text-gray-500">{ts('dash_as_of', language).replace('{time}', formatTime(data.as_of, language))}</p>
          </div>
        )}
      </div>

      <Card className="p-4 bg-white border border-gray-200 rounded-md">
        <div className="flex flex-wrap items-end gap-4">
          <PeriodFilter value={{ range, from, to }} onChange={setPeriod} language={language} idPrefix="dash-period" />
          {canPickEmployee && (
            <div className="min-w-[12rem]">
              <Label htmlFor="dash-employee" className="text-xs text-gray-600">{ts('filter_employee', language)}</Label>
              <Select value={employee || ALL} onValueChange={(v) => setEmployee(v === ALL ? '' : v)}>
                <SelectTrigger id="dash-employee" className="mt-1 h-10" data-testid="dash-employee"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{ts('filter_all_employees', language)}</SelectItem>
                  {ref.assignees.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        {data && data.period && (
          <p className="text-xs text-gray-500 mt-3" data-testid="dashboard-period-echo">
            {ts('period_showing', language).replace('{from}', formatDay(data.period.date_from, language)).replace('{to}', formatDay(data.period.date_to, language))} · {ts('period_tz_note', language)}
          </p>
        )}
      </Card>

      {problem && <Card className="p-4 bg-red-50 border border-red-200 text-red-800 text-sm" role="alert" data-testid="dashboard-period-problem">{ts(problem.key, language).replace('{n}', problem.n)}</Card>}
      {errorText && <Card className="p-4 bg-red-50 border border-red-200 text-red-800 text-sm" role="alert" data-testid="dashboard-period-error">{errorText}</Card>}
      {error && !errorText && (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid="dashboard-error">
          <p className="text-gray-600 mb-4">{ts('dash_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button>
        </Card>
      )}
      {loading && !data && !error && <div className="text-center py-12 text-gray-500" data-testid="dashboard-loading">{ts('loading', language)}</div>}

      {data && <DashboardBody data={data} language={language} showDefinitions={showDefinitions} setShowDefinitions={setShowDefinitions} />}
    </div>
  );
};

const DashboardBody = ({ data, language, showDefinitions, setShowDefinitions }) => {
  const { kpis } = data;
  const hasLeadFigures = kpis.total_leads !== null && kpis.total_leads !== undefined;
  const cards = KPIS.filter((k) => {
    if (k.key === 'conversion_rate') return hasLeadFigures;              // null with leads = "no data"; null without lead access = not shown
    return kpis[k.key] !== null && kpis[k.key] !== undefined;
  });
  const anything = cards.length > 0 || data.pipeline || data.performance || data.sources || data.campaigns || data.activity || data.onboarding;

  if (!anything) {
    return <Card className="p-10 text-center bg-white border border-gray-200 text-gray-500" data-testid="dashboard-nothing">{ts('dash_nothing', language)}</Card>;
  }

  return (
    <>
      {cards.length > 0 && (
        <section aria-label={ts('sec_kpis', language)} data-testid="section-kpis">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {cards.map((k) => {
              const value = kpis[k.key];
              return (
                <KpiCard
                  key={k.key} id={k.key} label={kpiLabel(k.key, language)} basis={k.basis} tone={k.tone} language={language}
                  value={k.percent ? formatPercent(value, language) : formatCount(value, language)}
                  alert={k.alertWhenPositive && value > 0}
                  definition={kpiDefinition(k, language, stageName)}
                />
              );
            })}
          </div>
        </section>
      )}

      {data.pipeline && (
        <Section id="pipeline" title={ts('sec_pipeline', language)} basis="cohort" hint={ts('def_pipeline', language)} language={language}
          actions={<Button asChild variant="outline" size="sm" className="rounded-sm"><Link to="/sales/pipeline">{ts('dash_view_board', language)}</Link></Button>}>
          <BarList testid="pipeline-bars" language={language} rows={data.pipeline.map((s) => ({
            key: s.stage, label: stageName(s.stage, language), value: s.leads,
            detail: s.value ? `${ts('rc_value', language)}: ${formatMoney(s.value, language)} · ${ts('rc_valued', language)}: ${formatCount(s.valued, language)}` : null,
          }))} />
        </Section>
      )}

      {data.performance && (
        <Section id="performance" title={ts('sec_performance', language)} basis="cohort" language={language}
          hint={data.performance.total > data.performance.items.length ? ts('rc_more_items', language).replace('{shown}', formatCount(data.performance.items.length, language)).replace('{total}', formatCount(data.performance.total, language)) : null}>
          {data.performance.items.length === 0
            ? <p className="text-sm text-gray-500" data-testid="performance-empty">{ts('rep_empty', language)}</p>
            : <PerformanceTable rows={data.performance.items} language={language} testid="performance-table" />}
        </Section>
      )}

      {(data.sources || data.campaigns) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {data.sources && (
            <Section id="sources" title={ts('sec_sources', language)} basis="cohort" language={language}>
              {data.sources.length === 0
                ? <p className="text-sm text-gray-500" data-testid="sources-empty">{ts('rep_empty', language)}</p>
                : <BarList testid="source-bars" language={language} rows={data.sources.map((s) => ({
                  key: s.source, label: language === 'ar' ? s.name_ar : s.name_en, value: s.leads,
                  detail: `${ts('rc_won', language)}: ${formatCount(s.won, language)} · ${ts('rc_conversion', language)}: ${formatPercent(s.conversion_rate, language)}`,
                }))} />}
            </Section>
          )}
          {data.campaigns && (
            <Section id="campaigns" title={ts('sec_campaigns', language)} basis="cohort" language={language}
              hint={data.campaigns.total > data.campaigns.items.length ? ts('rc_more_items', language).replace('{shown}', formatCount(data.campaigns.items.length, language)).replace('{total}', formatCount(data.campaigns.total, language)) : null}>
              {data.campaigns.items.length === 0 && data.campaigns.no_campaign.leads === 0
                ? <p className="text-sm text-gray-500" data-testid="campaigns-empty">{ts('rep_empty', language)}</p>
                : <BarList testid="campaign-bars" language={language} rows={[
                  ...data.campaigns.items.map((c) => ({
                    key: c.campaign_id, label: c.name, value: c.leads,
                    detail: `${ts('rc_won', language)}: ${formatCount(c.won, language)} · ${ts('rc_conversion', language)}: ${formatPercent(c.conversion_rate, language)}`,
                  })),
                  ...(data.campaigns.no_campaign.leads > 0 ? [{
                    key: 'none', label: ts('rc_no_campaign', language), value: data.campaigns.no_campaign.leads,
                    detail: `${ts('rc_won', language)}: ${formatCount(data.campaigns.no_campaign.won, language)}`,
                  }] : []),
                ]} />}
            </Section>
          )}
        </div>
      )}

      {data.activity && (
        <Section id="activity" title={ts('sec_activity', language)} basis="event" hint={ts('def_activity', language)} language={language}>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[['calls', 'nav_calls'], ['followups', 'nav_followups'], ['demos', 'nav_demos'], ['trials', 'nav_trials']]
              .filter(([k]) => data.activity[k] !== null && data.activity[k] !== undefined)
              .map(([k, labelKey]) => (
                <div key={k} className="rounded-md border border-gray-200 p-3" data-testid={`activity-${k}`}>
                  <p className="text-2xl font-bold text-[#0A0A0A]" data-testid={`activity-${k}-value`}>{formatCount(data.activity[k], language)}</p>
                  <p className="text-sm text-gray-600">{ts(labelKey, language)}</p>
                </div>
              ))}
            <div className="rounded-md border border-blue-200 bg-blue-50 p-3" data-testid="activity-total">
              <p className="text-2xl font-bold text-[#0033A0]" data-testid="activity-total-value">{formatCount(data.activity.total, language)}</p>
              <p className="text-sm text-gray-700">{ts('rc_activities', language)}</p>
            </div>
          </div>
        </Section>
      )}

      {data.onboarding && (
        <Section id="onboarding" title={ts('sec_onboarding', language)} hint={ts('def_onboarding', language)} language={language}>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
            <Figure id="onb-active" value={data.onboarding.active} label={ts('rc_active', language)} basis="now" language={language} />
            <Figure id="onb-activated" value={data.onboarding.activated_in_period} label={ts('rc_activated', language)} basis="event" language={language} />
            {data.onboarding.unassigned !== null && data.onboarding.unassigned !== undefined && (
              <Figure id="onb-unassigned" value={data.onboarding.unassigned} label={ts('rep_onb_unowned', language)} basis="now" language={language} alert={data.onboarding.unassigned > 0} />
            )}
            <Figure id="onb-total" value={data.onboarding.total} label={ts('rc_total', language)} basis="now" language={language} />
          </div>
          <BarList testid="onboarding-bars" language={language} rows={Object.entries(data.onboarding.by_stage).map(([stage, n]) => ({ key: stage, label: onbStageName(stage, language), value: n }))} />
        </Section>
      )}

      <div>
        <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setShowDefinitions(!showDefinitions)} aria-expanded={showDefinitions} data-testid="definitions-toggle">
          {ts(showDefinitions ? 'sec_hide_definitions' : 'sec_show_definitions', language)}
        </Button>
        {showDefinitions && <Definitions language={language} cards={cards} />}
      </div>
    </>
  );
};

const Figure = ({ id, value, label, basis, alert = false, language }) => (
  <div className={`rounded-md border p-3 ${alert ? 'border-red-200 bg-red-50' : 'border-gray-200'}`} data-testid={id}>
    <p className={`text-2xl font-bold ${alert ? 'text-red-700' : 'text-[#0A0A0A]'}`} data-testid={`${id}-value`}>{formatCount(value, language)}</p>
    <p className="text-sm text-gray-600">{label}</p>
    <div className="mt-1"><BasisPill basis={basis} language={language} /></div>
  </div>
);

// The definitions of every figure on the page - the same texts as the card tooltips, readable without hovering.
const Definitions = ({ language, cards }) => (
  <Card className="mt-3 p-5 bg-white border border-gray-200 rounded-md" data-testid="definitions">
    <h2 className="text-lg font-semibold text-[#0A0A0A] mb-3">{ts('sec_definitions', language)}</h2>
    <dl className="space-y-3 text-sm">
      {cards.map((k) => (
        <div key={k.key}>
          <dt className="font-medium text-[#0A0A0A]">{kpiLabel(k.key, language)} <span className="ms-1"><BasisPill basis={k.basis} language={language} /></span></dt>
          <dd className="text-gray-600 mt-0.5">{kpiDefinition(k, language, stageName)}</dd>
        </div>
      ))}
      {['cohort', 'event', 'now'].map((b) => (
        <div key={b}>
          <dt className="font-medium text-[#0A0A0A]">{basisName(b, language)}</dt>
          <dd className="text-gray-600 mt-0.5">{basisHint(b, language)}</dd>
        </div>
      ))}
      <div>
        <dt className="font-medium text-[#0A0A0A]">{ts('rc_value', language)}</dt>
        <dd className="text-gray-600 mt-0.5">{ts('def_value', language)}</dd>
      </div>
    </dl>
  </Card>
);

export default SalesDashboard;
