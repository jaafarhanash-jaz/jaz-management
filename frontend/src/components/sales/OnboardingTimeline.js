import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import CustomerEventDetails, { CUSTOMER_EVENT_META } from '@/components/sales/CustomerEventDetails';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { History } from 'lucide-react';

// An onboarding record's own immutable timeline (GET /sales/onboarding/{id}/timeline, newest first): the assignments, stage
// changes and notes of THIS onboarding - never the lead's own history, which an Onboarding Employee is not entitled to.
const PAGE = 20;
const FALLBACK_META = { icon: History, style: 'bg-gray-50 text-gray-700 border-gray-200' };

const OnboardingTimeline = ({ onboardingId, language, refreshKey = 0 }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (offset, replace) => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get(`/sales/onboarding/${onboardingId}/timeline`, { params: { limit: PAGE, offset } });
      setItems((prev) => (replace ? res.data.items : [...prev, ...res.data.items]));
      setTotal(res.data.total);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setLoading(false);
  }, [onboardingId, language]);

  useEffect(() => { load(0, true); }, [load, refreshKey]);

  if (error) {
    return (
      <div className="text-sm text-red-600" role="alert" data-testid="onb-timeline-error">
        {error} <Button variant="outline" size="sm" className="rounded-sm ms-2" onClick={() => load(0, true)}>{ts('action_retry', language)}</Button>
      </div>
    );
  }
  if (loading && items.length === 0) return <p className="text-sm text-gray-500">{ts('loading', language)}</p>;
  if (items.length === 0) return <p className="text-sm text-gray-500" data-testid="onb-timeline-empty">{ts('onb_timeline_empty', language)}</p>;

  return (
    <div data-testid="onb-timeline">
      <ol className="relative border-s border-gray-200 ms-3 space-y-5">
        {items.map((event) => {
          const meta = CUSTOMER_EVENT_META[event.event_type] || FALLBACK_META;
          const Icon = meta.icon;
          return (
            <li key={event.id} className="ps-6 relative" data-testid={`onb-timeline-event-${event.event_type}`}>
              <span className={`absolute -start-3.5 top-0 flex h-7 w-7 items-center justify-center rounded-full border ${meta.style}`}>
                <Icon className="w-3.5 h-3.5" aria-hidden="true" />
              </span>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-[#0A0A0A]">{CUSTOMER_EVENT_META[event.event_type] ? ts(`event_${event.event_type}`, language) : event.event_type}</span>
                <span className="text-xs text-gray-500">
                  {ts('timeline_by', language)} <bdi>{event.actor?.name || '-'}</bdi> · <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at, language)}</time>
                </span>
              </div>
              <CustomerEventDetails event={event} language={language} />
              {event.note && <p className="mt-1 text-sm text-gray-600 italic" dir="auto">“{event.note}”</p>}
            </li>
          );
        })}
      </ol>
      {items.length < total && (
        <div className="mt-4 text-center">
          <Button variant="outline" size="sm" className="rounded-sm" disabled={loading} onClick={() => load(items.length, false)} data-testid="onb-timeline-more">
            {ts('action_load_more', language)}
          </Button>
        </div>
      )}
    </div>
  );
};

export default OnboardingTimeline;
