import { CustomerStatusBadge, OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { ts } from '@/utils/salesTranslations';
import { ArrowRight, ClipboardCheck, Handshake, NotebookPen, UserPlus } from 'lucide-react';

// The timeline events written by conversion and onboarding (Phase 4): their icon and colour, and how each one describes what
// happened. Read-only, like the whole timeline. The server only sends `lead_converted` to a reader who may view customers
// and the onboarding events to a reader who may view onboarding, so anybody else simply never sees them.
const GREEN = 'bg-green-50 text-green-700 border-green-200';
const BLUE = 'bg-blue-50 text-blue-700 border-blue-200';
const VIOLET = 'bg-violet-50 text-violet-700 border-violet-200';
const GRAY = 'bg-gray-50 text-gray-700 border-gray-200';

export const CUSTOMER_EVENT_META = {
  lead_converted: { icon: Handshake, style: GREEN },
  onboarding_assigned: { icon: UserPlus, style: BLUE },
  onboarding_stage_changed: { icon: ClipboardCheck, style: VIOLET },
  onboarding_notes_updated: { icon: NotebookPen, style: GRAY },
};

// <bdi> isolates each value's own direction, so an English name inside an Arabic sentence never reorders the words around it.
const Value = ({ children }) => <bdi className="font-medium text-[#0A0A0A]">{children}</bdi>;
const Arrow = () => <ArrowRight className="inline w-3.5 h-3.5 mx-1 text-gray-400 rtl:rotate-180" aria-hidden="true" />;
const Dot = () => <span className="text-gray-400"> · </span>;
const line = 'mt-1 text-sm text-gray-700';

const CustomerEventDetails = ({ event, language }) => {
  const { event_type: type, before, after } = event;

  if (type === 'lead_converted' && after) {
    // (a confirmed duplicate override - metadata.duplicate_override - is shown by the timeline itself, for every kind of event)
    return (
      <p className={line} data-testid="conv-event-details">
        {ts('tl_conv_company', language)}: <Value>{after.company_name}</Value>
        {after.plan && <><Dot />{ts('tl_conv_plan', language)}: <Value>{after.plan}</Value></>}
        {after.customer_status && <><Dot /><CustomerStatusBadge status={after.customer_status} language={language} /></>}
      </p>
    );
  }
  if (type === 'onboarding_assigned' && after) {
    return (
      <p className={`${line} flex flex-wrap items-center gap-x-1.5 gap-y-1`}>
        {before?.assigned_to && <><bdi className="text-gray-500 line-through">{before.assigned_to.name}</bdi><Arrow /></>}
        {after.assigned_to && <Value>{after.assigned_to.name}</Value>}
        {before?.stage && after.stage && before.stage !== after.stage && (
          <><Dot /><OnboardingStageBadge stage={before.stage} language={language} /><Arrow /><OnboardingStageBadge stage={after.stage} language={language} /></>
        )}
      </p>
    );
  }
  if (type === 'onboarding_stage_changed' && before && after) {
    return (
      <p className={`${line} flex flex-wrap items-center gap-x-1.5 gap-y-1`}>
        <OnboardingStageBadge stage={before.stage} language={language} /><Arrow /><OnboardingStageBadge stage={after.stage} language={language} />
        {before.customer_status !== after.customer_status && (
          <><Dot />{ts('tl_onb_status', language)}: <CustomerStatusBadge status={after.customer_status} language={language} /></>
        )}
      </p>
    );
  }
  if (type === 'onboarding_notes_updated' && after) {
    return (
      <p className={line} dir="auto">
        <span className="text-gray-500">{ts('tl_onb_notes', language)}: </span>
        {after.notes ? <Value>{after.notes}</Value> : <span className="text-gray-400">{ts('timeline_empty_value', language)}</span>}
      </p>
    );
  }
  return null;
};

export default CustomerEventDetails;
