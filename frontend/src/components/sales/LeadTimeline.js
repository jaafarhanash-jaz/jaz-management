import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { StageBadge } from '@/components/sales/LeadBadges';
import WorkEventDetails, { WORK_EVENT_META } from '@/components/sales/WorkEventDetails';
import CustomerEventDetails, { CUSTOMER_EVENT_META } from '@/components/sales/CustomerEventDetails';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { formatDateTime, formatValue, lostReasonName, priorityName, sourceName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { Archive, ArchiveRestore, ArrowRight, ArrowRightLeft, CircleX, History, Hourglass, Pencil, Plus, Trophy, UserMinus, UserPlus } from 'lucide-react';

// The lead's immutable activity timeline (GET /sales/leads/{id}/activities, newest first): what happened, who did it,
// when, and the before/after values the server recorded. Read-only by construction - there is nothing to edit here.
const PAGE = 20;

const EVENT_META = {
  lead_created: { icon: Plus, style: 'bg-green-50 text-green-700 border-green-200' },
  lead_updated: { icon: Pencil, style: 'bg-blue-50 text-blue-700 border-blue-200' },
  lead_assigned: { icon: UserPlus, style: 'bg-blue-50 text-blue-700 border-blue-200' },
  lead_reassigned: { icon: ArrowRightLeft, style: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
  lead_unassigned: { icon: UserMinus, style: 'bg-gray-50 text-gray-700 border-gray-200' },
  stage_changed: { icon: ArrowRight, style: 'bg-violet-50 text-violet-700 border-violet-200' },
  lead_marked_won: { icon: Trophy, style: 'bg-green-50 text-green-700 border-green-200' },
  lead_marked_lost: { icon: CircleX, style: 'bg-red-50 text-red-700 border-red-200' },
  lead_archived: { icon: Archive, style: 'bg-gray-50 text-gray-700 border-gray-200' },
  lead_restored: { icon: ArchiveRestore, style: 'bg-green-50 text-green-700 border-green-200' },
  lead_wait_listed: { icon: Hourglass, style: 'bg-amber-50 text-amber-700 border-amber-200' },
  ...WORK_EVENT_META,                     // Phase 3: calls, follow-ups, demos, trials
  ...CUSTOMER_EVENT_META,                 // Phase 4: conversion and onboarding
};
const FALLBACK_META = { icon: History, style: 'bg-gray-50 text-gray-700 border-gray-200' };

const fieldValue = (field, value, language, sources) => {
  if (value === null || value === undefined || value === '') return ts('timeline_empty_value', language);
  if (field === 'source') return sourceName(sources, value, language);
  if (field === 'priority') return priorityName(value, language);
  if (field === 'estimated_value') return formatValue(value, language);
  return String(value);
};

// <bdi> isolates each value's own direction, so an English phone number inside an Arabic sentence (or the reverse)
// never reorders the words around it.
const Value = ({ children }) => <bdi className="font-medium text-[#0A0A0A]">{children}</bdi>;

const UpdatedDetails = ({ event, language, sources }) => {
  const before = event.before || {};
  const after = event.after || {};
  const fields = Object.keys(after).filter((f) => f !== 'campaign_id');
  return (
    <ul className="mt-1 space-y-0.5 text-sm text-gray-700">
      {fields.map((f) => (
        <li key={f}>
          <span className="text-gray-500">{ts(`lead_field_${f === 'campaign_name' ? 'campaign' : f}`, language)}: </span>
          <bdi className="text-gray-500 line-through">{fieldValue(f, before[f], language, sources)}</bdi>
          <ArrowRight className="inline w-3.5 h-3.5 mx-1 text-gray-400 rtl:rotate-180" aria-hidden="true" />
          <Value>{fieldValue(f, after[f], language, sources)}</Value>
        </li>
      ))}
    </ul>
  );
};

const Details = ({ event, language, sources }) => {
  const { event_type: type, before, after, note, metadata } = event;
  const meta = metadata || {};
  return (
    <>
      {type === 'lead_created' && after && (
        <p className="mt-1 text-sm text-gray-700">
          {[
            ['lead_field_source', after.source && sourceName(sources, after.source, language)],
            ['lead_field_priority', after.priority && priorityName(after.priority, language)],
            ['lead_field_campaign', after.campaign_name],
          ].filter(([, value]) => value).map(([labelKey, value], i) => (
            <span key={labelKey}>{i > 0 && <span className="text-gray-400"> · </span>}{ts(labelKey, language)}: <Value>{value}</Value></span>
          ))}
        </p>
      )}
      {type === 'lead_updated' && <UpdatedDetails event={event} language={language} sources={sources} />}
      {(type === 'lead_assigned' || type === 'lead_reassigned' || type === 'lead_unassigned') && (
        <p className="mt-1 text-sm text-gray-700">
          {before?.assigned_to && <><bdi className="text-gray-500 line-through">{before.assigned_to.name}</bdi><ArrowRight className="inline w-3.5 h-3.5 mx-1 text-gray-400 rtl:rotate-180" aria-hidden="true" /></>}
          {after?.assigned_to ? <Value>{after.assigned_to.name}</Value> : <span className="text-gray-500">{ts('timeline_nobody', language)}</span>}
        </p>
      )}
      {type === 'stage_changed' && before && after && (
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-sm">
          <StageBadge stage={before.pipeline_stage} language={language} />
          <ArrowRight className="w-3.5 h-3.5 text-gray-400 rtl:rotate-180" aria-hidden="true" />
          <StageBadge stage={after.pipeline_stage} language={language} />
          {meta.automatic && <span className="text-xs text-gray-500">({ts('timeline_automatic', language)})</span>}
          {meta.reopened && <span className="text-xs text-gray-500">({ts('timeline_reopened', language)})</span>}
        </p>
      )}
      {type === 'lead_marked_lost' && after?.lost_reason && (
        <p className="mt-1 text-sm text-gray-700">{ts('lead_field_lost_reason', language)}: <Value>{lostReasonName(after.lost_reason, language)}</Value></p>
      )}
      {WORK_EVENT_META[type] && <WorkEventDetails event={event} language={language} />}
      {CUSTOMER_EVENT_META[type] && <CustomerEventDetails event={event} language={language} />}
      {note && <p className="mt-1 text-sm text-gray-600 italic" dir="auto">“{note}”</p>}
      {meta.duplicate_override && (
        <p className="mt-1 text-xs text-amber-700">
          {ts('timeline_dup_override', language).replace('{n}', (meta.duplicate_override.exact || 0) + (meta.duplicate_override.possible || 0))}
        </p>
      )}
    </>
  );
};

const LeadTimeline = ({ leadId, language, sources, refreshKey = 0 }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (offset, replace) => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get(`/sales/leads/${leadId}/activities`, { params: { limit: PAGE, offset } });
      setItems((prev) => (replace ? res.data.items : [...prev, ...res.data.items]));
      setTotal(res.data.total);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setLoading(false);
  }, [leadId, language]);

  useEffect(() => { load(0, true); }, [load, refreshKey]);

  if (error) {
    return (
      <div className="text-sm text-red-600" role="alert" data-testid="timeline-error">
        {error} <Button variant="outline" size="sm" className="rounded-sm ms-2" onClick={() => load(0, true)}>{ts('action_retry', language)}</Button>
      </div>
    );
  }
  if (loading && items.length === 0) return <p className="text-sm text-gray-500" data-testid="timeline-loading">{ts('loading', language)}</p>;
  if (items.length === 0) return <p className="text-sm text-gray-500" data-testid="timeline-empty">{ts('timeline_empty', language)}</p>;

  return (
    <div data-testid="lead-timeline">
      <ol className="relative border-s border-gray-200 ms-3 space-y-5">
        {items.map((event) => {
          const meta = EVENT_META[event.event_type] || FALLBACK_META;
          const Icon = meta.icon;
          const known = !!EVENT_META[event.event_type];
          return (
            <li key={event.id} className="ps-6 relative" data-testid={`timeline-event-${event.event_type}`}>
              <span className={`absolute -start-3.5 top-0 flex h-7 w-7 items-center justify-center rounded-full border ${meta.style}`}>
                <Icon className="w-3.5 h-3.5" aria-hidden="true" />
              </span>
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-[#0A0A0A]">{known ? ts(`event_${event.event_type}`, language) : event.event_type}</span>
                <span className="text-xs text-gray-500">
                  {ts('timeline_by', language)} <bdi>{event.actor?.name || '-'}</bdi> · <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at, language)}</time>
                </span>
              </div>
              <Details event={event} language={language} sources={sources} />
            </li>
          );
        })}
      </ol>
      {items.length < total && (
        <div className="mt-4 text-center">
          <Button variant="outline" size="sm" className="rounded-sm" disabled={loading} onClick={() => load(items.length, false)} data-testid="timeline-more">
            {ts('action_load_more', language)}
          </Button>
        </div>
      )}
    </div>
  );
};

export default LeadTimeline;
