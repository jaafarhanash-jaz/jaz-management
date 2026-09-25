import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { formatDate, formatValue, lostReasonName, sourceName, stageName } from '@/utils/salesLeads';
import { ACTIVITY_KINDS, formatCount, formatMoney, formatPercent } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { CampaignStatusBadge, PriorityBadge, StageBadge } from '@/components/sales/LeadBadges';
import { OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { BarList, TrendChart } from '@/components/sales/ReportParts';
import { DataTable, PerformanceTable } from '@/components/sales/ReportTables';

// One renderer per report: it turns the server's aggregated response into a summary, a chart where one helps, and a table.
// Nothing is computed here beyond formatting - the totals, rates and orderings all arrive from the API.

const Empty = ({ language }) => <p className="text-sm text-gray-500 py-4" data-testid="report-empty">{ts('rep_empty', language)}</p>;

const Stat = ({ id, label, value, hint = null, tone }) => (
  <div className="rounded-md border border-gray-200 p-3" data-testid={id}>
    <p className={`text-2xl font-bold ${tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : 'text-[#0A0A0A]'}`} data-testid={`${id}-value`}>{value}</p>
    <p className="text-sm text-gray-600">{label}</p>
    {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
  </div>
);

const Block = ({ title, children, testid }) => (
  <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid={testid}>
    {title && <h3 className="text-base font-semibold text-[#0A0A0A] mb-3">{title}</h3>}
    {children}
  </Card>
);

const figureColumns = (language, first = null) => [
  first,
  { key: 'leads', label: ts('rc_leads', language), numeric: true, render: (r) => formatCount(r.leads, language) },
  { key: 'won', label: ts('rc_won', language), numeric: true, render: (r) => formatCount(r.won, language) },
  { key: 'lost', label: ts('rc_lost', language), numeric: true, render: (r) => formatCount(r.lost, language) },
  { key: 'open', label: ts('rc_open', language), numeric: true, render: (r) => formatCount(r.open, language) },
  { key: 'conversion', label: ts('rc_conversion', language), numeric: true, render: (r) => formatPercent(r.conversion_rate, language) },
  { key: 'value', label: ts('rc_value', language), numeric: true, render: (r) => formatMoney(r.value, language) },
  { key: 'won_value', label: ts('rc_won_value', language), numeric: true, render: (r) => formatMoney(r.won_value, language) },
];

// ---- 1. leads -----------------------------------------------------------------------------------------------------------
export const LeadsView = ({ data, language, sources }) => (
  <div className="space-y-4">
    <Block testid="leads-summary" title={ts('rep_leads_summary', language).replace('{n}', formatCount(data.summary.total, language)).replace('{value}', formatMoney(data.summary.value, language))}>
      <BarList testid="leads-by-stage" language={language} rows={Object.entries(data.summary.by_stage).map(([stage, n]) => ({ key: stage, label: stageName(stage, language), value: n }))} />
    </Block>
    <Block testid="leads-table-block">
      {data.items.length === 0 ? <Empty language={language} /> : (
        <DataTable testid="leads-table" rowKey={(r) => r.id} rows={data.items} caption={ts('rep_name_leads', language)} columns={[
          { key: 'business', label: ts('rc_business', language), render: (r) => <Link to={`/sales/leads/${r.id}`} className="text-[#0033A0] hover:underline break-words">{r.business_name}</Link> },
          { key: 'stage', label: ts('rc_stage', language), render: (r) => <StageBadge stage={r.pipeline_stage} language={language} /> },
          { key: 'source', label: ts('rc_source', language), render: (r) => sourceName(sources, r.source, language) },
          { key: 'campaign', label: ts('rc_campaign', language), render: (r) => (r.campaign ? r.campaign.name : '-') },
          { key: 'owner', label: ts('rc_assigned', language), render: (r) => (r.assigned_to ? r.assigned_to.name : <span className="text-gray-500">{ts('unassigned', language)}</span>) },
          { key: 'priority', label: ts('rc_priority', language), render: (r) => <PriorityBadge priority={r.priority} language={language} /> },
          { key: 'value', label: ts('rc_value', language), numeric: true, render: (r) => formatValue(r.estimated_value, language) },
          { key: 'created', label: ts('rc_created', language), render: (r) => formatDate(r.created_at, language) },
        ]} />
      )}
    </Block>
  </div>
);

// ---- 2. pipeline --------------------------------------------------------------------------------------------------------
export const PipelineView = ({ data, language }) => (
  <Block testid="pipeline-report">
    <div className="grid grid-cols-2 gap-3 mb-5">
      <Stat id="pipeline-total-leads" label={ts('rc_leads', language)} value={formatCount(data.total_leads, language)} />
      <Stat id="pipeline-total-value" label={ts('rc_value', language)} value={formatMoney(data.total_value, language)} hint={ts('def_value', language)} />
    </div>
    <BarList testid="pipeline-report-bars" language={language} rows={data.stages.map((s) => ({
      key: s.stage, label: stageName(s.stage, language), value: s.leads,
      detail: s.value ? `${ts('rc_value', language)}: ${formatMoney(s.value, language)} · ${ts('rc_valued', language)}: ${formatCount(s.valued, language)}` : null,
    }))} />
  </Block>
);

// ---- 3. employee performance ---------------------------------------------------------------------------------------------
export const EmployeesView = ({ data, language }) => (
  <Block testid="employees-block">
    {data.items.length === 0 ? <Empty language={language} /> : <PerformanceTable rows={data.items} totals={data.totals} language={language} testid="employees-table" />}
  </Block>
);

// ---- 4. lead sources -------------------------------------------------------------------------------------------------------
export const SourcesView = ({ data, language }) => (
  <Block testid="sources-block">
    {data.items.length === 0 ? <Empty language={language} /> : (
      <DataTable testid="sources-table" rowKey={(r) => r.source} rows={data.items} caption={ts('rep_name_sources', language)}
        columns={figureColumns(language, { key: 'source', label: ts('rc_source', language), render: (r) => (language === 'ar' ? r.name_ar : r.name_en) })}
        footerRow={(c) => {
          if (c.key === 'source') return ts('rep_totals', language);
          if (c.key === 'conversion') return formatPercent(data.totals.conversion_rate, language);
          const t = { leads: data.totals.leads, won: data.totals.won, lost: data.totals.lost, open: data.totals.open, value: data.totals.value, won_value: data.totals.won_value }[c.key];
          return c.key === 'value' || c.key === 'won_value' ? formatMoney(t, language) : formatCount(t, language);
        }} />
    )}
  </Block>
);

// ---- 5. campaigns -----------------------------------------------------------------------------------------------------------
export const CampaignsView = ({ data, language, sources }) => (
  <div className="space-y-4">
    <Block testid="campaigns-block">
      {data.items.length === 0 ? <Empty language={language} /> : (
        <DataTable testid="campaigns-table" rowKey={(r) => r.campaign_id} rows={data.items} caption={ts('rep_name_campaigns', language)} columns={[
          { key: 'campaign', label: ts('rc_campaign', language), render: (r) => r.name },
          { key: 'status', label: ts('rc_status', language), render: (r) => <CampaignStatusBadge status={r.status} language={language} /> },
          { key: 'source', label: ts('rc_source', language), render: (r) => (r.source ? sourceName(sources, r.source, language) : '-') },
          ...figureColumns(language).slice(1),                                    // the figure columns after the (here separate) name column
        ]} />
      )}
    </Block>
    {data.no_campaign && (
      <Block testid="no-campaign" title={ts('rep_no_campaign_row', language)}>
        <div className="grid grid-cols-3 gap-3">
          <Stat id="no-campaign-leads" label={ts('rc_leads', language)} value={formatCount(data.no_campaign.leads, language)} />
          <Stat id="no-campaign-won" label={ts('rc_won', language)} value={formatCount(data.no_campaign.won, language)} />
          <Stat id="no-campaign-lost" label={ts('rc_lost', language)} value={formatCount(data.no_campaign.lost, language)} />
        </div>
      </Block>
    )}
  </div>
);

// ---- 6. conversion -------------------------------------------------------------------------------------------------------------
export const ConversionView = ({ data, language }) => (
  <div className="space-y-4">
    <Block title={ts('rep_conv_cohort', language)} testid="conversion-cohort">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat id="conv-leads" label={ts('rc_leads', language)} value={formatCount(data.cohort.leads, language)} />
        <Stat id="conv-won" label={ts('rc_won', language)} value={formatCount(data.cohort.won, language)} tone="good" />
        <Stat id="conv-lost" label={ts('rc_lost', language)} value={formatCount(data.cohort.lost, language)} tone="bad" />
        <Stat id="conv-open" label={ts('rc_open', language)} value={formatCount(data.cohort.open, language)} />
        <Stat id="conv-rate" label={ts('rc_conversion', language)} value={formatPercent(data.cohort.conversion_rate, language)} hint={ts('def_conversion_rate', language)} />
      </div>
    </Block>
    <Block title={ts('rep_conv_closed', language)} testid="conversion-closed">
      <p className="text-xs text-gray-500 -mt-2 mb-3">{ts('rep_conv_closed_hint', language)}</p>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Stat id="closed-won" label={ts('rc_won', language)} value={formatCount(data.closed_in_period.won, language)} tone="good" />
        <Stat id="closed-lost" label={ts('rc_lost', language)} value={formatCount(data.closed_in_period.lost, language)} tone="bad" />
        {data.customers !== null && data.customers !== undefined && <Stat id="conv-customers" label={ts('rep_conv_customers', language)} value={formatCount(data.customers, language)} />}
      </div>
    </Block>
    <Block title={`${ts('rep_conv_trend', language)} - ${ts(data.series.bucket === 'day' ? 'rep_conv_bucket_day' : 'rep_conv_bucket_week', language)}`} testid="conversion-trend-block">
      {data.series.points.length === 0 ? <Empty language={language} /> : <TrendChart points={data.series.points} language={language} />}
    </Block>
    {data.lost_reasons.length > 0 && (
      <Block title={ts('rep_conv_lost_reasons', language)} testid="conversion-lost-reasons">
        <BarList testid="lost-reasons" language={language} rows={data.lost_reasons.map((r) => ({ key: r.reason, label: lostReasonName(r.reason, language), value: r.leads }))} />
      </Block>
    )}
  </div>
);

// ---- 7. activities -----------------------------------------------------------------------------------------------------------------
const BREAKDOWN_LABEL = {
  calls: (k) => `call_result_${k}`, followups: (k) => `followup_status_${k}`, demos: (k) => `demo_status_${k}`, trials: (k) => `trial_status_${k}`,
};
const KIND_LABEL = { calls: 'nav_calls', followups: 'nav_followups', demos: 'nav_demos', trials: 'nav_trials' };

export const ActivitiesView = ({ data, language }) => {
  const kinds = ACTIVITY_KINDS.filter((k) => data.kinds[k]);
  return (
    <div className="space-y-4">
      <Block testid="activities-totals">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {kinds.map((k) => <Stat key={k} id={`act-${k}`} label={ts(KIND_LABEL[k], language)} value={formatCount(data.kinds[k].total, language)} />)}
          <Stat id="act-total" label={ts('rc_activities', language)} value={formatCount(data.activities, language)} tone="good" />
        </div>
        <p className="text-xs text-gray-500 mt-3">{ts('rep_act_cancelled_note', language)}</p>
      </Block>
      <Block title={ts('rep_act_breakdown', language)} testid="activities-breakdown">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {kinds.map((k) => (
            <div key={k} data-testid={`breakdown-${k}`}>
              <h4 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts(KIND_LABEL[k], language)}</h4>
              <BarList testid={`breakdown-${k}-bars`} language={language} rows={Object.entries(data.kinds[k].breakdown).map(([key, n]) => ({ key, label: ts(BREAKDOWN_LABEL[k](key), language), value: n }))} />
            </div>
          ))}
        </div>
      </Block>
      <Block testid="activities-people">
        {data.items.length === 0 ? <Empty language={language} /> : (
          <DataTable testid="activities-table" rowKey={(r) => r.employee.id} rows={data.items} caption={ts('rep_name_activities', language)} columns={[
            { key: 'employee', label: ts('rc_employee', language), render: (r) => r.employee.name },
            ...kinds.map((k) => ({ key: k, label: ts(`rc_${k}`, language), numeric: true, render: (r) => formatCount(r[k], language) })),
            { key: 'activities', label: ts('rc_activities', language), numeric: true, render: (r) => formatCount(r.activities, language) },
          ]} />
        )}
      </Block>
    </div>
  );
};

// ---- 8. onboarding ---------------------------------------------------------------------------------------------------------------------
export const OnboardingView = ({ data, language }) => (
  <div className="space-y-4">
    <Block title={ts('rep_onb_started_in_period', language)} testid="onboarding-summary">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <Stat id="onb-total" label={ts('rc_total', language)} value={formatCount(data.summary.total, language)} />
        <Stat id="onb-open" label={ts('rc_active', language)} value={formatCount(data.summary.open, language)} />
        <Stat id="onb-activated" label={ts('rc_activated', language)} value={formatCount(data.summary.activated, language)} tone="good" />
        {data.summary.unassigned !== null && data.summary.unassigned !== undefined && <Stat id="onb-unassigned" label={ts('rep_onb_unowned', language)} value={formatCount(data.summary.unassigned, language)} />}
      </div>
      <h4 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('rep_onb_by_stage', language)}</h4>
      <BarList testid="onboarding-report-bars" language={language} rows={Object.entries(data.summary.by_stage).map(([stage, n]) => ({ key: stage, label: ts(`onb_stage_${stage}`, language), value: n }))} />
    </Block>
    {data.by_owner.length > 0 && (
      <Block title={ts('rep_onb_by_owner', language)} testid="onboarding-owners">
        {data.by_owner_total > data.by_owner.length && (
          <p className="text-xs text-gray-500 -mt-2 mb-3" data-testid="onboarding-owners-more">
            {ts('rc_more_items', language).replace('{shown}', formatCount(data.by_owner.length, language)).replace('{total}', formatCount(data.by_owner_total, language))}
          </p>
        )}
        <DataTable testid="onboarding-owners-table" rowKey={(r) => (r.employee ? r.employee.id : 'none')} rows={data.by_owner} columns={[
          { key: 'employee', label: ts('rc_employee', language), render: (r) => (r.employee ? r.employee.name : <span className="text-gray-500">{ts('rep_onb_unowned', language)}</span>) },
          { key: 'total', label: ts('rc_total', language), numeric: true, render: (r) => formatCount(r.total, language) },
          { key: 'open', label: ts('rc_active', language), numeric: true, render: (r) => formatCount(r.open, language) },
          { key: 'activated', label: ts('rc_activated', language), numeric: true, render: (r) => formatCount(r.activated, language) },
        ]} />
      </Block>
    )}
    <Block testid="onboarding-records">
      {data.items.length === 0 ? <Empty language={language} /> : (
        <DataTable testid="onboarding-table" rowKey={(r) => r.id} rows={data.items} caption={ts('rep_name_onboarding', language)} columns={[
          { key: 'business', label: ts('rc_business', language), render: (r) => r.business_name },
          { key: 'stage', label: ts('rc_stage', language), render: (r) => <OnboardingStageBadge stage={r.stage} language={language} /> },
          { key: 'owner', label: ts('rc_assigned', language), render: (r) => (r.assigned_to ? r.assigned_to.name : <span className="text-gray-500">{ts('rep_onb_unowned', language)}</span>) },
          { key: 'started', label: ts('rc_started', language), render: (r) => formatDate(r.started_at, language) },
          { key: 'completed', label: ts('rc_completed', language), render: (r) => formatDate(r.completed_at, language) },
        ]} />
      )}
    </Block>
  </div>
);
