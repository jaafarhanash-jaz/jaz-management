import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { appDate } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';

// A short "your work" overview on the workspace home: a few numbers - overdue follow-ups, scheduled demos, active trials,
// recent calls, customers and onboarding - each linking to the view behind it. One counts request per kind the caller may view; a failed request
// just leaves that tile out. The numbers are computed by the server, within the caller's lead scope.
const weekAgo = () => appDate(7);        // a date in the application timezone (Baghdad), like every Sales date filter

const SOURCES = [
  {
    module: 'followups', path: '/sales/followups/counts',
    tiles: (c) => [
      { key: 'followups-overdue', labelKey: 'tile_followups_overdue', value: c.mine_overdue, to: '/sales/followups?view=overdue&person=me', alert: c.mine_overdue > 0 },
      { key: 'followups-pending', labelKey: 'tile_followups_pending', value: c.mine_pending, to: '/sales/followups' },
    ],
  },
  {
    module: 'demos', path: '/sales/demos/counts',
    tiles: (c) => [
      { key: 'demos-upcoming', labelKey: 'tile_demos_upcoming', value: c.mine_scheduled, to: '/sales/demos?person=me' },
      { key: 'demos-past-due', labelKey: 'tile_demos_past_due', value: c.past_due, to: '/sales/demos', alert: c.past_due > 0 },
    ],
  },
  {
    module: 'trials', path: '/sales/trials/counts',
    tiles: (c) => [
      { key: 'trials-active', labelKey: 'tile_trials_active', value: c.active, to: '/sales/trials' },
      { key: 'trials-ending', labelKey: 'tile_trials_ending', value: c.ending_soon, to: '/sales/trials?view=ending', alert: c.ending_soon > 0 },
    ],
  },
  {
    module: 'calls', path: '/sales/calls/result-counts', params: () => ({ called_from: weekAgo() }),
    tiles: (c) => [{ key: 'calls-week', labelKey: 'tile_calls_week', value: c.total, to: `/sales/calls?called_from=${weekAgo()}` }],
  },
  {
    module: 'customers', path: '/sales/customers/counts',
    tiles: (c) => [{ key: 'customers-total', labelKey: 'tile_customers_total', value: c.total, to: '/sales/customers' }],
  },
  {
    module: 'onboarding', path: '/sales/onboarding/counts',
    tiles: (c, can) => [
      { key: 'onboarding-open', labelKey: 'tile_onboarding_open', value: c.open, to: '/sales/onboarding' },
      // the unowned records exist only in the scope of somebody who sees every record
      ...(can('sales.onboarding.scope_all') ? [{ key: 'onboarding-unassigned', labelKey: 'tile_onboarding_unassigned', value: c.unassigned, to: '/sales/onboarding?view=unassigned', alert: c.unassigned > 0 }] : []),
      { key: 'onboarding-mine', labelKey: 'tile_onboarding_mine', value: c.mine, to: '/sales/onboarding?view=mine' },
    ],
  },
];

const WorkOverview = ({ language }) => {
  const { hasModule, can } = useSalesAccess();
  const [tiles, setTiles] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const sources = SOURCES.filter((s) => hasModule(s.module));
    Promise.all(sources.map((s) => api.get(s.path, { params: s.params ? s.params() : {} }).then((res) => s.tiles(res.data, can)).catch(() => [])))
      .then((groups) => { if (!cancelled) setTiles(groups.flat()); });
    return () => { cancelled = true; };
  }, [hasModule, can]);

  if (!tiles || tiles.length === 0) return null;
  return (
    <div data-testid="work-overview">
      <h2 className="text-sm text-gray-500 mb-2">{ts('home_work_title', language)}</h2>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {tiles.map((t) => (
          <Link key={t.key} to={t.to} data-testid={`tile-${t.key}`} aria-label={`${ts(t.labelKey, language)}: ${t.value}`}>
            <Card className={`p-4 rounded-md border h-full hover:bg-gray-50 ${t.alert ? 'bg-red-50 border-red-200' : 'bg-white border-gray-200'}`}>
              <p className={`text-3xl font-bold ${t.alert ? 'text-red-700' : 'text-[#0A0A0A]'}`} data-testid={`tile-${t.key}-value`}>{t.value}</p>
              <p className="text-sm text-gray-600 mt-1">{ts(t.labelKey, language)}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
};

export default WorkOverview;
