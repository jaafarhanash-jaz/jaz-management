import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from '@/components/ui/card';
import { basisHint, basisName, formatCount } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';

// Small presentational pieces shared by the dashboard and the reports: the basis pill, a KPI card, a section frame, a bar
// list and the trend chart. Nothing here fetches or decides anything.

// Which kind of number this is (see backend/sales/metrics.py). Every pill carries its text label, so colour is never the
// only signal.
const BASIS_STYLES = {
  cohort: 'bg-gray-50 text-gray-700 border-gray-200',
  event: 'bg-blue-50 text-[#0033A0] border-blue-200',
  now: 'bg-amber-50 text-amber-800 border-amber-200',
};

export const BasisPill = ({ basis, language }) => (
  <span
    className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-medium border ${BASIS_STYLES[basis] || BASIS_STYLES.cohort}`}
    title={basisHint(basis, language)} data-testid={`basis-${basis}`}
  >
    {basisName(basis, language)}
  </span>
);

export const KpiCard = ({ id, label, value, basis, definition, alert = false, tone, language }) => {
  const valueColor = alert ? 'text-red-700' : tone === 'good' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : 'text-[#0A0A0A]';
  return (
    <Card className={`p-4 rounded-md border h-full ${alert ? 'bg-red-50 border-red-200' : 'bg-white border-gray-200'}`} data-testid={`kpi-${id}`} title={definition}>
      <p className={`text-3xl font-bold ${valueColor}`} data-testid={`kpi-${id}-value`}>{value}</p>
      <p className="text-sm text-gray-600 mt-1">{label}</p>
      <div className="mt-2"><BasisPill basis={basis} language={language} /></div>
    </Card>
  );
};

export const Section = ({ id, title, basis, hint, language, children, actions = null }) => (
  <section aria-labelledby={`sec-${id}`} data-testid={`section-${id}`}>
    <Card className="p-5 bg-white border border-gray-200 rounded-md">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
        <div>
          <h2 id={`sec-${id}`} className="text-lg font-semibold text-[#0A0A0A]">{title}</h2>
          {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
        </div>
        <div className="flex items-center gap-2">
          {basis && <BasisPill basis={basis} language={language} />}
          {actions}
        </div>
      </div>
      {children}
    </Card>
  </section>
);

// A horizontal bar list: label, a bar proportional to the largest value, the number, and a line of detail underneath. Plain
// boxes rather than a chart, so it follows the page direction (RTL / LTR) by itself and is read as text by a screen reader.
export const BarList = ({ rows, language, testid }) => {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="space-y-3" data-testid={testid}>
      {rows.map((r) => (
        <li key={r.key} data-testid={`${testid}-${r.key}`}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-gray-700 break-words min-w-0">{r.label}</span>
            <span className="font-semibold text-[#0A0A0A] shrink-0" data-testid={`${testid}-${r.key}-value`}>{formatCount(r.value, language)}</span>
          </div>
          <div className="h-2 rounded-full bg-gray-100 mt-1" aria-hidden="true">
            <div className="h-2 rounded-full bg-[#0033A0]" style={{ width: `${r.value ? Math.max(2, (r.value / max) * 100) : 0}%` }} />
          </div>
          {r.detail && <p className="text-xs text-gray-500 mt-0.5">{r.detail}</p>}
        </li>
      ))}
    </ul>
  );
};

// Leads created vs won per day / week. Time runs left to right in both languages (an axis of dates is read that way), so the
// chart sits in an explicit LTR box.
export const TrendChart = ({ points, language }) => {
  const data = points.map((p) => ({ label: p.start.slice(5), leads: p.leads, won: p.won }));
  return (
    <div dir="ltr" className="h-64 w-full" role="img" aria-label={ts('rep_conv_trend', language)} data-testid="conversion-trend">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
          <YAxis allowDecimals={false} width={48} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Bar dataKey="leads" name={ts('rep_legend_leads', language)} fill="#0033A0" radius={[2, 2, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="won" name={ts('rep_legend_won', language)} fill="#15803d" radius={[2, 2, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
