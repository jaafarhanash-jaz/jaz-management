// JAZ Sales - Phase 5 (dashboard and reports): the vocabulary and the small helpers shared by the dashboard and the reports
// page. Nothing here decides what a caller may see or what a number means - the server does both (and returns null for a
// section the caller may not see); this file only lists, labels and formats. The metric definitions live in
// backend/sales/metrics.py; the texts that explain them are the def_* strings in salesReportsTranslations.js.
import { ts } from '@/utils/salesTranslations';

export const RANGES = ['today', 'this_week', 'this_month', 'last_month', 'custom'];
export const DEFAULT_RANGE = 'this_month';
export const MAX_PERIOD_DAYS = 366;

const DAY_MS = 24 * 60 * 60 * 1000;
const locale = (language) => (language === 'ar' ? 'ar-EG' : 'en-US');

export const rangeName = (range, language) => ts(`range_${range}`, language);

// The query parameters of a period. A preset is sent as `range` alone; a custom range as its two dates.
export const periodParams = ({ range, from, to }) => (
  range === 'custom' ? { range: 'custom', date_from: from, date_to: to } : { range: range || DEFAULT_RANGE }
);

// Friendly client-side check of a custom range (the server is the authority and answers 400 with a stable code).
// Returns the translation key of the problem, or null.
export const customPeriodProblem = (from, to) => {
  if (!from || !to) return { key: 'period_error_incomplete' };
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return { key: 'period_error_incomplete' };
  if (a > b) return { key: 'period_error_reversed' };
  if ((b - a) / DAY_MS + 1 > MAX_PERIOD_DAYS) return { key: 'period_error_too_long', n: MAX_PERIOD_DAYS };
  return null;
};

// The message for an API error about the period ({field, code, message}); anything else falls back to the generic text.
export const periodErrorText = (detail, language) => {
  const code = detail && detail.code;
  if (code === 'period_incomplete') return ts('period_error_incomplete', language);
  if (code === 'period_reversed') return ts('period_error_reversed', language);
  if (code === 'period_too_long') return ts('period_error_too_long', language).replace('{n}', MAX_PERIOD_DAYS);
  return ts('period_error_generic', language);
};

// ---- formatting ---------------------------------------------------------------------------------------------------
export const formatCount = (n, language) => (n === null || n === undefined ? '-' : Number(n).toLocaleString(locale(language)));

// A rate is a percentage with one decimal, or null ("no data") - which is shown as a dash, never as 0 %.
export const formatPercent = (rate, language) => (
  rate === null || rate === undefined ? '-' : `${Number(rate).toLocaleString(locale(language), { maximumFractionDigits: 1, minimumFractionDigits: 0 })}%`
);

export const formatMoney = (value, language) => (
  value === null || value === undefined ? '-' : Number(value).toLocaleString(locale(language), { maximumFractionDigits: 2 })
);

// "2026-09-01" -> a readable date in the user's language (parsed as UTC so the day never shifts).
export const formatDay = (iso, language) => (
  iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString(locale(language), { dateStyle: 'medium', timeZone: 'UTC' }) : '-'
);

export const formatTime = (iso, language) => (
  iso ? new Date(iso).toLocaleTimeString(locale(language), { hour: '2-digit', minute: '2-digit' }) : '-'
);

// The application timezone of JAZ Sales: Baghdad, UTC+3 all year (mirrors backend/sales/timezone.py - one place to change).
// A calendar DATE in Sales - a period, a date filter, "7 days ago" - is a date in that zone, whatever the browser's own zone is.
export const APP_UTC_OFFSET_HOURS = 3;
// "YYYY-MM-DD" of the day that is `daysAgo` days before now in the application timezone.
export const appDate = (daysAgo = 0, now = Date.now()) => new Date(now + APP_UTC_OFFSET_HOURS * 3600000 - daysAgo * 86400000).toISOString().slice(0, 10);

// ---- the dashboard's key figures, in the order the dashboard shows them ------------------------------------------------
// `stages` are the pipeline stages a cohort KPI counts (for its definition text); `basis` matches METRIC_BASES on the server.
export const KPIS = [
  { key: 'total_leads', basis: 'cohort', def: 'def_total_leads' },
  { key: 'new_leads', basis: 'cohort', stages: ['new'] },
  { key: 'assigned_leads', basis: 'cohort', stages: ['assigned'] },
  { key: 'contacted_leads', basis: 'cohort', stages: ['contacted'] },
  { key: 'interested_leads', basis: 'cohort', stages: ['interested'] },
  { key: 'demos', basis: 'cohort', stages: ['demo_scheduled', 'demo_completed'] },
  { key: 'negotiations', basis: 'cohort', stages: ['negotiation'] },
  { key: 'won', basis: 'cohort', stages: ['won'], tone: 'good' },
  { key: 'lost', basis: 'cohort', stages: ['lost'], tone: 'bad' },
  { key: 'conversion_rate', basis: 'cohort', def: 'def_conversion_rate', percent: true },
  { key: 'active_trials', basis: 'now', def: 'def_active_trials' },
  { key: 'upcoming_demos', basis: 'now', def: 'def_upcoming_demos' },
  { key: 'overdue_followups', basis: 'now', def: 'def_overdue_followups', alertWhenPositive: true },
  { key: 'upcoming_followups', basis: 'now', def: 'def_upcoming_followups' },
];

export const kpiLabel = (key, language) => ts(`kpi_${key}`, language);

// The definition text of one KPI (a stage KPI gets its stage names filled in).
export const kpiDefinition = (kpi, language, stageName) => {
  if (kpi.def) return ts(kpi.def, language);
  const names = (kpi.stages || []).map((s) => stageName(s, language)).join(' / ');
  return ts('def_stage', language).replace('{stages}', names);
};

export const ACTIVITY_KINDS = ['calls', 'followups', 'demos', 'trials'];
export const BASES = ['cohort', 'event', 'now'];
export const basisName = (basis, language) => ts(`basis_${basis}`, language);
export const basisHint = (basis, language) => ts(`basis_${basis}_hint`, language);

// ---- the reports, in menu order ---------------------------------------------------------------------------------------
// `available(can)` mirrors the server's permission keys for the menu only (the API enforces them regardless); `filters`
// are the extra filters the report accepts, `paged` whether its result is a list with pages.
const has = (can, ...keys) => keys.every((k) => can(k));
export const REPORTS = [
  { key: 'leads', path: 'leads', nameKey: 'rep_name_leads', descKey: 'rep_desc_leads', filters: ['employee', 'source', 'campaign', 'stage', 'priority'], paged: true,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view') },
  { key: 'pipeline', path: 'pipeline', nameKey: 'rep_name_pipeline', descKey: 'rep_desc_pipeline', filters: ['employee', 'source', 'campaign', 'basis'], paged: false,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view') },
  { key: 'employees', path: 'employee-performance', nameKey: 'rep_name_employees', descKey: 'rep_desc_employees', filters: ['employee', 'source', 'campaign'], paged: true,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view', 'sales.leads.scope_all') },
  { key: 'sources', path: 'sources', nameKey: 'rep_name_sources', descKey: 'rep_desc_sources', filters: ['employee', 'campaign'], paged: false,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view') },
  { key: 'campaigns', path: 'campaigns', nameKey: 'rep_name_campaigns', descKey: 'rep_desc_campaigns', filters: ['employee', 'source', 'campaign', 'campaign_status'], paged: true,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view', 'sales.campaigns.view') },
  { key: 'conversion', path: 'conversion', nameKey: 'rep_name_conversion', descKey: 'rep_desc_conversion', filters: ['employee', 'source', 'campaign'], paged: false,
    available: (can) => has(can, 'sales.reports.view', 'sales.leads.view') },
  { key: 'activities', path: 'activities', nameKey: 'rep_name_activities', descKey: 'rep_desc_activities', filters: ['employee'], paged: true,
    available: (can) => can('sales.reports.view') && ['calls', 'followups', 'demos', 'trials'].some((k) => can(`sales.${k}.view`)) },
  { key: 'onboarding', path: 'onboarding', nameKey: 'rep_name_onboarding', descKey: 'rep_desc_onboarding', filters: ['onb_stage', 'onb_employee'], paged: true,
    // Simplified workflow: onboarding is not part of the active workflow any more - the report stays (API and view) but is not offered.
    available: () => false },
];

export const REPORT_PAGE_SIZE = 25;
