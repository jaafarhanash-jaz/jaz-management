import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { CallCard, DemoCard, FollowupCard, TrialCard } from '@/components/sales/ActivityItems';
import { useCallActions, useDemoActions, useFollowupActions, useTrialActions } from '@/components/sales/WorkActions';
import { AlertCircle, FlaskConical, ListChecks, PhoneCall, Plus, Presentation } from 'lucide-react';

// The work done on ONE lead - follow-ups, calls, demos, trials - on the lead's page: a tab per kind the caller may view,
// each with its items (open ones first, then the most recent finished ones), and the "add" button for those the caller
// may manage. Every button on an item follows `item.can` from the server. Changes are reported to the page through
// `onChanged(eventType, item)` so it can refresh the timeline and offer the matching pipeline stage (never automatic).
const FINISHED_LIMIT = 10;

const KINDS = [
  { key: 'followups', icon: ListChecks, lateKey: 'act_overdue', labelKey: 'nav_followups', addKey: 'followups_add', emptyKey: 'work_empty_followups', open: { status: 'pending', sort: 'due_at', order: 'asc' }, done: { status: 'completed,cancelled', sort: 'due_at', order: 'desc' } },
  { key: 'calls', icon: PhoneCall, lateKey: 'act_overdue', labelKey: 'nav_calls', addKey: 'calls_add', emptyKey: 'work_empty_calls', open: { order: 'desc' }, done: null },
  { key: 'demos', icon: Presentation, lateKey: 'act_past_due', labelKey: 'nav_demos', addKey: 'demos_add', emptyKey: 'work_empty_demos', open: { status: 'scheduled', order: 'asc' }, done: { status: 'completed,cancelled,no_show', order: 'desc' } },
  { key: 'trials', icon: FlaskConical, lateKey: 'act_overdue', labelKey: 'nav_trials', addKey: 'trials_add', emptyKey: 'work_empty_trials', open: { status: 'active', sort: 'expected_end_at', order: 'asc' }, done: { status: 'completed,cancelled', sort: 'actual_end_at', order: 'desc' } },
];
const CARD = { followups: FollowupCard, calls: CallCard, demos: DemoCard, trials: TrialCard };

const LeadActivities = ({ lead, language, onChanged }) => {
  const { can, hasModule } = useSalesAccess();
  const visible = KINDS.filter((k) => hasModule(k.key));
  const [tab, setTab] = useState(visible[0]?.key);
  const [items, setItems] = useState(null);           // null = loading
  const [counts, setCounts] = useState({});
  const [error, setError] = useState(false);
  const requestRef = useRef(0);
  const archived = !!lead.archived_at;

  const load = useCallback(async () => {
    if (!tab) return;
    const id = ++requestRef.current;
    setError(false);
    const base = { lead_id: lead.id };
    const kind = KINDS.find((k) => k.key === tab);
    try {
      const requests = [api.get(`/sales/${tab}`, { params: { ...base, ...kind.open, limit: 50 } })];
      if (kind.done) requests.push(api.get(`/sales/${tab}`, { params: { ...base, ...kind.done, limit: FINISHED_LIMIT } }));
      const [openRes, doneRes] = await Promise.all(requests);
      if (id !== requestRef.current) return;
      setItems([...openRes.data.items, ...(doneRes ? doneRes.data.items : [])]);
    } catch (e) {
      if (id === requestRef.current) setError(true);
    }
  }, [tab, lead.id]);

  const loadCounts = useCallback(async () => {
    const entries = await Promise.all(visible.map(async (k) => {
      try {
        const path = k.key === 'calls' ? '/sales/calls/result-counts' : `/sales/${k.key}/counts`;
        const res = await api.get(path, { params: { lead_id: lead.id } });
        const c = res.data;
        return [k.key, { followups: { open: c.pending, overdue: c.overdue }, calls: { open: c.total }, demos: { open: c.scheduled, overdue: c.past_due }, trials: { open: c.active, overdue: c.overdue } }[k.key]];
      } catch (e) {
        return [k.key, null];
      }
    }));
    setCounts(Object.fromEntries(entries));
  }, [lead.id, visible.map((k) => k.key).join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setItems(null); load(); }, [load]);
  useEffect(() => { loadCounts(); }, [loadCounts]);

  const changed = (event, item) => {
    load();
    loadCounts();
    onChanged && onChanged(event, item);
  };
  const followups = useFollowupActions({ language, lead, onChanged: changed });
  const calls = useCallActions({ language, lead, onChanged: changed });
  const demos = useDemoActions({ language, lead, onChanged: changed });
  const trials = useTrialActions({ language, lead, onChanged: changed });
  const actions = { followups, calls, demos, trials };

  if (visible.length === 0) return null;
  const Card_ = CARD[tab];
  const current = KINDS.find((k) => k.key === tab);
  const canAdd = can(`sales.${tab}.manage`) && !archived;

  return (
    <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-activities">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[#0A0A0A]">{ts('work_title', language)}</h2>
          <p className="text-xs text-gray-500 mt-0.5">{ts('work_hint', language)}</p>
        </div>
        {canAdd && (
          <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={actions[tab].create} data-testid={`add-${tab}-btn`}>
            <Plus className="w-4 h-4 me-2" aria-hidden="true" />{ts(current.addKey, language)}
          </Button>
        )}
      </div>

      <div className="overflow-x-auto mt-4" data-testid="work-tabs">
        <div role="tablist" aria-label={ts('work_title', language)} className="flex gap-2 min-w-max pb-1">
          {visible.map((k) => {
            const Icon = k.icon;
            const c = counts[k.key];
            return (
              <button
                key={k.key} type="button" role="tab" aria-selected={tab === k.key} onClick={() => setTab(k.key)} data-testid={`work-tab-${k.key}`}
                className={`inline-flex items-center gap-1.5 px-3 py-2 min-h-[40px] rounded-full text-sm border whitespace-nowrap ${tab === k.key ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'}`}
              >
                <Icon className="w-4 h-4" aria-hidden="true" />{ts(k.labelKey, language)}
                {c && <span className="opacity-80" data-testid={`work-count-${k.key}`}>({c.open})</span>}
                {c && c.overdue > 0 && (
                  <span className={`inline-flex items-center gap-0.5 rounded-full px-1.5 text-xs ${tab === k.key ? 'bg-white/20' : 'bg-red-50 text-red-700'}`}
                    title={ts(k.lateKey, language)} aria-label={`${ts(k.lateKey, language)}: ${c.overdue}`} data-testid={`work-overdue-${k.key}`}>
                    <AlertCircle className="w-3 h-3" aria-hidden="true" />{c.overdue}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-4" role="tabpanel" data-testid={`work-panel-${tab}`}>
        {error ? (
          <p className="text-sm text-red-600" role="alert">{ts('act_error', language)} <Button variant="outline" size="sm" className="rounded-sm ms-2" onClick={load}>{ts('action_retry', language)}</Button></p>
        ) : items === null ? (
          <p className="text-sm text-gray-500">{ts('loading', language)}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-gray-500" data-testid={`work-empty-${tab}`}>{ts(current.emptyKey, language)}</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => <li key={item.id}><Card_ item={item} actions={actions[tab]} language={language} showLead={false} /></li>)}
          </ul>
        )}
      </div>

      {followups.dialogs}
      {calls.dialogs}
      {demos.dialogs}
      {trials.dialogs}
    </Card>
  );
};

export default LeadActivities;
