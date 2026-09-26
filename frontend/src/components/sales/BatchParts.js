import { StageBadge } from '@/components/sales/LeadBadges';
import { formatCount, formatPercent } from '@/utils/salesReports';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';

// Small pieces shared by the Sales Manager's Batches and Performance pages. Everything shown here comes from the server's
// statistics (services/batch_views.py): the UI never computes a result, it only lays the numbers out.

export const fill = (text, values) => Object.entries(values).reduce((out, [k, v]) => out.replace(`{${k}}`, v), text);

const STATUS_STYLE = {
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  full: 'bg-green-50 text-green-700 border-green-200',
  closed: 'bg-green-50 text-green-700 border-green-200',
};

export const BatchStatus = ({ status, language }) => (
  <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] || 'bg-gray-50 text-gray-700 border-gray-200'}`}
    data-testid={`batch-status-${status}`}>
    {ts(`bt_status_${status}`, language)}
  </span>
);

const PATH_STYLE = {
  direct_accepted: 'bg-green-50 text-green-700 border-green-200',
  direct_rejected: 'bg-red-50 text-red-700 border-red-200',
  wait_accepted: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  wait_rejected: 'bg-orange-50 text-orange-700 border-orange-200',
  wait_pending: 'bg-amber-50 text-amber-800 border-amber-200',
  released: 'bg-gray-50 text-gray-600 border-gray-200',
};

export const PathBadge = ({ path, language }) => (
  <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${PATH_STYLE[path] || PATH_STYLE.released}`} data-testid={`path-${path}`}>
    {ts(`bt_path_${path}`, language)}
  </span>
);

const Cell = ({ label, value, pct, tone, testid }) => (
  <div className={`rounded-md border px-3 py-2 ${tone}`} data-testid={testid}>
    <p className="text-[11px] leading-tight text-gray-600">{label}</p>
    <p className="mt-0.5 text-lg font-semibold tabular-nums text-[#0A0A0A]">
      {value}{pct !== undefined && <span className="ms-1.5 text-xs font-normal text-gray-500">{pct}</span>}
    </p>
  </div>
);

// The accepted / rejected split of a set of leads: accepted or rejected DIRECTLY, and after a stay on the wait list, with the
// percentages of the decided leads. `compact` = one line of chips (the table cells).
export const StatsGrid = ({ stats, language, testid = 'stats' }) => {
  const n = (v) => formatCount(v, language);
  const p = (v) => formatPercent(v, language);
  return (
    <div data-testid={testid}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Cell testid={`${testid}-accepted-direct`} label={ts('bt_stat_accepted_direct', language)} value={n(stats.accepted_direct)} pct={p(stats.pct_accepted_direct)} tone="border-green-200 bg-green-50/50" />
        <Cell testid={`${testid}-rejected-direct`} label={ts('bt_stat_rejected_direct', language)} value={n(stats.rejected_direct)} pct={p(stats.pct_rejected_direct)} tone="border-red-200 bg-red-50/50" />
        <Cell testid={`${testid}-wait-accepted`} label={ts('bt_stat_wait_accepted', language)} value={n(stats.wait_accepted)} pct={p(stats.pct_wait_accepted)} tone="border-emerald-200 bg-emerald-50/50" />
        <Cell testid={`${testid}-wait-rejected`} label={ts('bt_stat_wait_rejected', language)} value={n(stats.wait_rejected)} pct={p(stats.pct_wait_rejected)} tone="border-orange-200 bg-orange-50/50" />
      </div>
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
        <span>{ts('bt_stat_accepted', language)}: <b className="tabular-nums">{n(stats.accepted)}</b> ({p(stats.pct_accepted)})</span>
        <span>{ts('bt_stat_rejected', language)}: <b className="tabular-nums">{n(stats.rejected)}</b> ({p(stats.pct_rejected)})</span>
        <span data-testid={`${testid}-pending`}>{ts('bt_stat_wait_pending', language)}: <b className="tabular-nums">{n(stats.wait_pending)}</b></span>
        {stats.released > 0 && <span>{ts('bt_stat_released', language)}: <b className="tabular-nums">{n(stats.released)}</b></span>}
        {stats.not_started > 0 && <span>{fill(ts('bt_not_started', language), { n: n(stats.not_started) })}</span>}
      </p>
    </div>
  );
};

// One line of the same split, for a table cell.
export const StatsLine = ({ stats, language }) => (
  <span className="whitespace-nowrap text-xs tabular-nums text-gray-700" data-testid="stats-line">
    <span className="text-green-700">{formatCount(stats.accepted_direct, language)}</span>
    {' / '}<span className="text-red-700">{formatCount(stats.rejected_direct, language)}</span>
    {' / '}<span className="text-emerald-700">{formatCount(stats.wait_accepted, language)}</span>
    {' / '}<span className="text-orange-700">{formatCount(stats.wait_rejected, language)}</span>
    {stats.wait_pending > 0 && <span className="ms-1 text-amber-700">(+{formatCount(stats.wait_pending, language)})</span>}
  </span>
);

export const StatsLegend = ({ language }) => (
  <p className="text-xs text-gray-500" data-testid="stats-legend">
    <span className="text-green-700">{ts('bt_stat_accepted_direct', language)}</span> / <span className="text-red-700">{ts('bt_stat_rejected_direct', language)}</span>
    {' / '}<span className="text-emerald-700">{ts('bt_stat_wait_accepted', language)}</span> / <span className="text-orange-700">{ts('bt_stat_wait_rejected', language)}</span>
    {' - '}{ts('bt_stat_percent_hint', language)}
  </p>
);

// A lead's attempts, oldest first: who tried it, what they first decided, how it ended and when.
export const AttemptsList = ({ attempts, language }) => {
  if (!attempts || attempts.length === 0) return <p className="text-xs text-gray-500">{ts('bt_no_attempts', language)}</p>;
  return (
    <ol className="space-y-1.5" data-testid="lead-attempts">
      {attempts.map((a) => (
        <li key={a.attempt_no} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-700">
          <PathBadge path={a.path} language={language} />
          <bdi className="font-medium text-[#0A0A0A]">{a.employee.name}</bdi>
          {a.recorded_by.id !== a.employee.id && <span className="text-gray-500">({fill(ts('bt_attempt_by', language), { name: a.recorded_by.name })})</span>}
          {a.wait_listed_at
            ? <span className="text-gray-500">{fill(ts('bt_attempt_wait_at', language), { date: formatDateTime(a.wait_listed_at, language) })}</span>
            : <span className="text-gray-500">{fill(ts('bt_attempt_at', language), { date: formatDateTime(a.outcome_at, language) })}</span>}
          {a.wait_listed_at && a.result_at && <span className="text-gray-500">{fill(ts('bt_attempt_result_at', language), { date: formatDateTime(a.result_at, language) })}</span>}
          {a.work_batch_seq && <span className="text-gray-400">#{a.work_batch_seq}.{a.batch_attempt_no}</span>}
        </li>
      ))}
    </ol>
  );
};

// The columns every "leads of a batch" table shows.
export const leadColumns = (language) => [
  { key: 'business', label: ts('bt_col_business', language), render: (l) => <bdi className="font-medium">{l.business_name}</bdi> },
  { key: 'type', label: ts('bt_col_type', language), render: (l) => (l.business_type ? <bdi>{l.business_type}</bdi> : '-') },
  { key: 'contact', label: ts('bt_col_contact', language), render: (l) => (l.contact_name ? <bdi>{l.contact_name}</bdi> : '-') },
  { key: 'phone', label: ts('bt_col_phone', language), render: (l) => (l.phone ? <bdi dir="ltr">{l.phone}</bdi> : '-') },
  { key: 'stage', label: ts('bt_col_stage', language), render: (l) => <StageBadge stage={l.pipeline_stage} language={language} /> },
  { key: 'owner', label: ts('bt_col_owner', language), render: (l) => (l.assigned_to ? <bdi>{l.assigned_to.name}</bdi> : '-') },
  { key: 'entered_by', label: ts('bt_col_entered_by', language), render: (l) => <bdi>{l.entered_by.name}</bdi> },
  { key: 'entered_at', label: ts('bt_col_entered_at', language), render: (l) => <span className="whitespace-nowrap">{formatDateTime(l.entered_at, language)}</span> },
];
