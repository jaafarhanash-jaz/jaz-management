import { CallResultBadge } from '@/components/sales/ActivityBadges';
import { callResultName, formatDuration } from '@/utils/salesActivities';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import {
  ArrowRight, Ban, CalendarCheck, CalendarClock, CalendarPlus, CircleCheck, CircleX, FlaskConical, ListChecks, ListPlus, PhoneCall, PhoneIncoming, Pencil, Timer, UserX,
} from 'lucide-react';

// The timeline events written by calls, follow-ups, demos and trials (Phase 3): their icon and colour, and how each one
// describes what happened. Read-only, like the whole timeline. The server only sends these events to a reader who may
// view that kind of work, so a reader without the permission simply never sees them.
const BLUE = 'bg-blue-50 text-blue-700 border-blue-200';
const GREEN = 'bg-green-50 text-green-700 border-green-200';
const GRAY = 'bg-gray-50 text-gray-700 border-gray-200';
const RED = 'bg-red-50 text-red-700 border-red-200';
const AMBER = 'bg-amber-50 text-amber-800 border-amber-200';
const TEAL = 'bg-teal-50 text-teal-700 border-teal-200';

export const WORK_EVENT_META = {
  call_created: { icon: PhoneCall, style: GREEN },
  call_updated: { icon: PhoneIncoming, style: BLUE },
  followup_created: { icon: ListPlus, style: BLUE },
  followup_updated: { icon: Pencil, style: BLUE },
  followup_completed: { icon: CircleCheck, style: GREEN },
  followup_cancelled: { icon: ListChecks, style: GRAY },
  demo_scheduled: { icon: CalendarPlus, style: AMBER },
  demo_rescheduled: { icon: CalendarClock, style: AMBER },
  demo_updated: { icon: Pencil, style: BLUE },
  demo_completed: { icon: CalendarCheck, style: GREEN },
  demo_cancelled: { icon: Ban, style: GRAY },
  demo_no_show: { icon: UserX, style: RED },
  trial_started: { icon: FlaskConical, style: TEAL },
  trial_updated: { icon: Pencil, style: BLUE },
  trial_completed: { icon: Timer, style: GREEN },
  trial_cancelled: { icon: CircleX, style: GRAY },
};

const TIME_FIELDS = ['due_at', 'called_at', 'scheduled_at', 'expected_end_at', 'started_at', 'actual_end_at', 'completed_at'];

const fieldText = (field, value, language) => {
  if (value === null || value === undefined || value === '') return ts('timeline_empty_value', language);
  if (field === 'result') return callResultName(value, language);
  if (field === 'duration_seconds') return formatDuration(value);
  if (TIME_FIELDS.includes(field)) return formatDateTime(value, language);
  if (field === 'assigned_to') return value.name;
  return String(value);
};

// <bdi> isolates each value's own direction, so an English name or a time inside an Arabic sentence never reorders it.
const Value = ({ children }) => <bdi className="font-medium text-[#0A0A0A]">{children}</bdi>;
const Arrow = () => <ArrowRight className="inline w-3.5 h-3.5 mx-1 text-gray-400 rtl:rotate-180" aria-hidden="true" />;
const Dot = () => <span className="text-gray-400"> · </span>;

const ChangedFields = ({ event, language }) => (
  <ul className="mt-1 space-y-0.5 text-sm text-gray-700">
    {Object.keys(event.after || {}).map((field) => (
      <li key={field}>
        <span className="text-gray-500">{ts(`work_field_${field}`, language)}: </span>
        <bdi className="text-gray-500 line-through">{fieldText(field, event.before?.[field], language)}</bdi>
        <Arrow />
        <Value>{fieldText(field, event.after[field], language)}</Value>
      </li>
    ))}
  </ul>
);

const WorkEventDetails = ({ event, language }) => {
  const { event_type: type, before, after, metadata } = event;
  const line = 'mt-1 text-sm text-gray-700';

  if (type.endsWith('_updated')) return <ChangedFields event={event} language={language} />;

  if (type === 'call_created' && after) {
    return (
      <p className={`${line} flex flex-wrap items-center gap-x-1.5 gap-y-1`}>
        <CallResultBadge result={after.result} language={language} />
        <Dot /><Value>{formatDateTime(after.called_at, language)}</Value>
        {after.duration_seconds !== undefined && <><Dot />{ts('timeline_duration', language)}: <Value><bdi dir="ltr">{formatDuration(after.duration_seconds)}</bdi></Value></>}
      </p>
    );
  }
  if ((type === 'followup_created' || type === 'demo_scheduled') && after) {
    const timeField = type === 'followup_created' ? 'due_at' : 'scheduled_at';
    return (
      <p className={line}>
        {ts(type === 'followup_created' ? 'timeline_due' : 'timeline_when', language)}: <Value>{formatDateTime(after[timeField], language)}</Value>
        {after.assigned_to && <><Dot />{ts('work_field_assigned_to', language)}: <Value>{after.assigned_to.name}</Value></>}
      </p>
    );
  }
  if (type === 'followup_completed' && metadata?.was_overdue) {
    return <p className="mt-1 text-xs text-amber-700">({ts('timeline_was_overdue', language)})</p>;
  }
  if (type === 'demo_rescheduled' && before && after) {
    return (
      <p className={line}>
        <bdi className="text-gray-500 line-through">{formatDateTime(before.scheduled_at, language)}</bdi><Arrow /><Value>{formatDateTime(after.scheduled_at, language)}</Value>
      </p>
    );
  }
  if ((type === 'demo_completed' || type === 'demo_cancelled' || type === 'demo_no_show') && before?.scheduled_at) {
    return <p className={line}>{ts('timeline_when', language)}: <Value>{formatDateTime(before.scheduled_at, language)}</Value></p>;
  }
  if (type === 'followup_cancelled' && before?.due_at) {
    return <p className={line}>{ts('timeline_due', language)}: <Value>{formatDateTime(before.due_at, language)}</Value></p>;
  }
  if (type === 'trial_started' && after) {
    return (
      <p className={line}>
        {ts('timeline_started', language)}: <Value>{formatDateTime(after.started_at, language)}</Value>
        <Dot />{ts('timeline_expected_end', language)}: <Value>{formatDateTime(after.expected_end_at, language)}</Value>
      </p>
    );
  }
  if ((type === 'trial_completed' || type === 'trial_cancelled') && after?.actual_end_at) {
    return <p className={line}>{ts('timeline_ended', language)}: <Value>{formatDateTime(after.actual_end_at, language)}</Value></p>;
  }
  return null;
};

export default WorkEventDetails;
