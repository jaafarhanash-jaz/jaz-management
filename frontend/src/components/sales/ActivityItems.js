import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CallResultBadge, DemoStatusBadge, FlagBadge, FollowupStatusBadge, TrialStatusBadge } from '@/components/sales/ActivityBadges';
import { ENDING_SOON_DAYS, DAY, formatDuration, relativeTime } from '@/utils/salesActivities';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { Ban, CalendarClock, Check, CircleX, Pencil, UserX } from 'lucide-react';

// How one call / follow-up / demo / trial looks - as a card (the lead page, and small screens) and as the pieces a table
// row is built from (the four list pages). Every button is shown only when the SERVER says the caller may do that to
// THIS item right now (`item.can`); the endpoints enforce the same rules regardless.

// ---- small shared pieces ---------------------------------------------------------------------------------
export const LeadLink = ({ lead, language }) => (
  <span className="inline-flex flex-wrap items-center gap-2 min-w-0">
    <Link to={`/sales/leads/${lead.id}`} className="font-medium text-[#0033A0] hover:underline break-words" data-testid={`lead-link-${lead.id}`}>{lead.business_name}</Link>
    {lead.archived && <span className="text-xs rounded-full border border-gray-300 px-2 py-0.5 text-gray-600">{ts('act_lead_archived', language)}</span>}
  </span>
);

// A moment: the absolute time (in its own direction, so digits never reorder an Arabic sentence) and how far away it is.
export const When = ({ iso, language, relative = true }) => (
  <span>
    <bdi className="text-[#0A0A0A]">{formatDateTime(iso, language)}</bdi>
    {relative && <span className="block text-xs text-gray-500">{relativeTime(iso, language)}</span>}
  </span>
);

export const Person = ({ person, language }) => (person
  ? (
    <span>
      {person.name}
      {person.status === 'inactive' && <span className="ms-1 text-xs text-red-600">({ts('assignee_inactive', language)})</span>}
    </span>
  )
  : <span className="text-gray-400">-</span>);

export const NotesText = ({ notes, language, clamp = true }) => (notes
  ? <p className={`text-sm text-gray-700 whitespace-pre-wrap break-words ${clamp ? 'line-clamp-3' : ''}`} dir="auto">{notes}</p>
  : <span className="text-sm text-gray-400">{ts('act_no_notes', language)}</span>);

const ActionButton = ({ icon: Icon, labelKey, onClick, language, testid, tone = 'default' }) => (
  <Button
    size="sm" variant="outline" onClick={onClick} data-testid={testid}
    className={`rounded-sm ${tone === 'danger' ? 'text-red-700 border-red-200 hover:bg-red-50' : ''} ${tone === 'primary' ? 'border-[#0033A0] text-[#0033A0] hover:bg-blue-50' : ''}`}
  >
    <Icon className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts(labelKey, language)}
  </Button>
);

// ---- calls -----------------------------------------------------------------------------------------------
export const CallActions = ({ item, actions, language }) => (item.can.update
  ? <div className="flex flex-wrap gap-1"><ActionButton icon={Pencil} labelKey="action_edit" onClick={() => actions.edit(item)} language={language} testid={`call-edit-${item.id}`} /></div>
  : null);

export const CallCard = ({ item, actions, language, showLead = true }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`call-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        {showLead && <LeadLink lead={item.lead} language={language} />}
        <p className="text-xs text-gray-600 mt-0.5">
          {ts('act_col_employee', language)}: <Person person={item.employee} language={language} />
          <span className="text-gray-400"> · </span>
          <bdi>{formatDateTime(item.called_at, language)}</bdi>
          <span className="text-gray-400"> · </span>
          {ts('act_col_duration', language)}: <bdi dir="ltr">{formatDuration(item.duration_seconds)}</bdi>
        </p>
      </div>
      <CallResultBadge result={item.result} language={language} />
    </div>
    {item.notes && <div className="mt-2"><NotesText notes={item.notes} language={language} /></div>}
    <div className="mt-3"><CallActions item={item} actions={actions} language={language} /></div>
  </Card>
);

// ---- follow-ups ------------------------------------------------------------------------------------------
export const FollowupActions = ({ item, actions, language }) => (
  <div className="flex flex-wrap gap-1">
    {item.can.complete && <ActionButton icon={Check} labelKey="followup_action_complete" tone="primary" onClick={() => actions.complete(item)} language={language} testid={`followup-complete-${item.id}`} />}
    {item.can.update && <ActionButton icon={Pencil} labelKey="action_edit" onClick={() => actions.edit(item)} language={language} testid={`followup-edit-${item.id}`} />}
    {item.can.cancel && <ActionButton icon={CircleX} labelKey="action_cancel" tone="danger" onClick={() => actions.cancel(item)} language={language} testid={`followup-cancel-${item.id}`} />}
  </div>
);

export const FollowupFlags = ({ item, language }) => (
  <span className="inline-flex flex-wrap gap-1.5">
    <FollowupStatusBadge status={item.status} language={language} />
    {item.is_overdue && <FlagBadge labelKey="act_overdue" language={language} testid={`followup-overdue-${item.id}`} />}
  </span>
);

export const FollowupCard = ({ item, actions, language, showLead = true }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`followup-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        {showLead && <div><LeadLink lead={item.lead} language={language} /></div>}
        <NotesText notes={item.notes} language={language} />
      </div>
      <FollowupFlags item={item} language={language} />
    </div>
    <p className="text-xs text-gray-600 mt-2">
      {ts('act_col_due', language)}: <bdi>{formatDateTime(item.due_at, language)}</bdi> ({relativeTime(item.due_at, language)})
      <span className="text-gray-400"> · </span>
      {ts('act_col_assignee', language)}: <Person person={item.assigned_to} language={language} />
    </p>
    <div className="mt-3"><FollowupActions item={item} actions={actions} language={language} /></div>
  </Card>
);

// ---- demos -----------------------------------------------------------------------------------------------
export const DemoActions = ({ item, actions, language }) => (
  <div className="flex flex-wrap gap-1">
    {item.can.complete && <ActionButton icon={Check} labelKey="demo_action_complete" tone="primary" onClick={() => actions.complete(item)} language={language} testid={`demo-complete-${item.id}`} />}
    {item.can.reschedule && <ActionButton icon={CalendarClock} labelKey="demo_action_reschedule" onClick={() => actions.reschedule(item)} language={language} testid={`demo-reschedule-${item.id}`} />}
    {item.can.update && <ActionButton icon={Pencil} labelKey="action_edit" onClick={() => actions.edit(item)} language={language} testid={`demo-edit-${item.id}`} />}
    {item.can.no_show && <ActionButton icon={UserX} labelKey="demo_action_no_show" tone="danger" onClick={() => actions.noShow(item)} language={language} testid={`demo-noshow-${item.id}`} />}
    {item.can.cancel && <ActionButton icon={Ban} labelKey="demo_action_cancel" tone="danger" onClick={() => actions.cancel(item)} language={language} testid={`demo-cancel-${item.id}`} />}
  </div>
);

export const DemoFlags = ({ item, language }) => (
  <span className="inline-flex flex-wrap gap-1.5">
    <DemoStatusBadge status={item.status} language={language} />
    {item.is_past_due && <FlagBadge labelKey="act_past_due" language={language} testid={`demo-pastdue-${item.id}`} />}
  </span>
);

export const DemoCard = ({ item, actions, language, showLead = true }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`demo-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        {showLead && <div><LeadLink lead={item.lead} language={language} /></div>}
        <NotesText notes={item.notes} language={language} />
      </div>
      <DemoFlags item={item} language={language} />
    </div>
    <p className="text-xs text-gray-600 mt-2">
      {ts('act_col_when', language)}: <bdi>{formatDateTime(item.scheduled_at, language)}</bdi> ({relativeTime(item.scheduled_at, language)})
      <span className="text-gray-400"> · </span>
      {ts('act_col_assignee', language)}: <Person person={item.assigned_to} language={language} />
    </p>
    <div className="mt-3"><DemoActions item={item} actions={actions} language={language} /></div>
  </Card>
);

// ---- trials ----------------------------------------------------------------------------------------------
export const TrialActions = ({ item, actions, language }) => (
  <div className="flex flex-wrap gap-1">
    {item.can.complete && <ActionButton icon={Check} labelKey="trial_action_complete" tone="primary" onClick={() => actions.complete(item)} language={language} testid={`trial-complete-${item.id}`} />}
    {item.can.update && <ActionButton icon={Pencil} labelKey="action_edit" onClick={() => actions.edit(item)} language={language} testid={`trial-edit-${item.id}`} />}
    {item.can.cancel && <ActionButton icon={CircleX} labelKey="action_cancel" tone="danger" onClick={() => actions.cancel(item)} language={language} testid={`trial-cancel-${item.id}`} />}
  </div>
);

// An active trial is "ending soon" when its expected end is within the server's window (or already behind us: overdue).
export const isEndingSoon = (item) => item.status === 'active' && !item.is_overdue && new Date(item.expected_end_at).getTime() - Date.now() <= ENDING_SOON_DAYS * DAY;

export const TrialFlags = ({ item, language }) => (
  <span className="inline-flex flex-wrap gap-1.5">
    <TrialStatusBadge status={item.status} language={language} />
    {item.is_overdue && <FlagBadge labelKey="act_trial_overdue" language={language} testid={`trial-overdue-${item.id}`} />}
    {isEndingSoon(item) && <FlagBadge labelKey="act_ending_soon" tone="amber" language={language} testid={`trial-ending-${item.id}`} />}
  </span>
);

export const TrialCard = ({ item, actions, language, showLead = true }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`trial-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        {showLead && <div><LeadLink lead={item.lead} language={language} /></div>}
        <NotesText notes={item.notes} language={language} />
      </div>
      <TrialFlags item={item} language={language} />
    </div>
    <p className="text-xs text-gray-600 mt-2">
      {ts('act_col_started', language)}: <bdi>{formatDateTime(item.started_at, language)}</bdi>
      <span className="text-gray-400"> · </span>
      {ts('act_col_expected_end', language)}: <bdi>{formatDateTime(item.expected_end_at, language)}</bdi>
      {item.status === 'active' && <> ({relativeTime(item.expected_end_at, language)})</>}
      {item.actual_end_at && <><span className="text-gray-400"> · </span>{ts('act_col_ended', language)}: <bdi>{formatDateTime(item.actual_end_at, language)}</bdi></>}
    </p>
    <div className="mt-3"><TrialActions item={item} actions={actions} language={language} /></div>
  </Card>
);
