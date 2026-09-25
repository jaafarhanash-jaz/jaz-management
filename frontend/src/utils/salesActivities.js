// JAZ Sales - Phase 3 (calls, follow-ups, demos, trials): the vocabulary and the small helpers shared by their screens.
// As in Phase 2, the RULES live on the server (each item carries `can`, the lead carries `allowed_stages`); nothing here
// decides whether an action is allowed - it only lists, labels, formats and converts things.
import { ts } from '@/utils/salesTranslations';
import { STAGES, stageName } from '@/utils/salesLeads';

// Mirrors backend/sales/constants.py.
export const CALL_RESULTS = ['answered', 'no_answer', 'busy', 'wrong_number', 'interested', 'not_interested', 'call_back'];
export const FOLLOWUP_STATUSES = ['pending', 'completed', 'cancelled'];
export const DEMO_STATUSES = ['scheduled', 'completed', 'cancelled', 'no_show'];
export const TRIAL_STATUSES = ['active', 'completed', 'cancelled'];
export const ENDING_SOON_DAYS = 3;     // the server's default window for "ending soon"

// Badge colours. Every badge also carries its text label, so colour is never the only signal.
export const CALL_RESULT_STYLES = {
  answered: 'bg-green-50 text-green-700 border-green-200',
  no_answer: 'bg-gray-50 text-gray-700 border-gray-200',
  busy: 'bg-amber-50 text-amber-800 border-amber-200',
  wrong_number: 'bg-red-50 text-red-700 border-red-200',
  interested: 'bg-violet-50 text-violet-700 border-violet-200',
  not_interested: 'bg-red-50 text-red-700 border-red-200',
  call_back: 'bg-sky-50 text-sky-700 border-sky-200',
};
export const FOLLOWUP_STATUS_STYLES = {
  pending: 'bg-blue-50 text-blue-700 border-blue-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  cancelled: 'bg-gray-50 text-gray-700 border-gray-200',
};
export const DEMO_STATUS_STYLES = {
  scheduled: 'bg-amber-50 text-amber-800 border-amber-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  cancelled: 'bg-gray-50 text-gray-700 border-gray-200',
  no_show: 'bg-red-50 text-red-700 border-red-200',
};
export const TRIAL_STATUS_STYLES = {
  active: 'bg-teal-50 text-teal-700 border-teal-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  cancelled: 'bg-gray-50 text-gray-700 border-gray-200',
};

export const callResultName = (result, language) => ts(`call_result_${result}`, language);
export const followupStatusName = (status, language) => ts(`followup_status_${status}`, language);
export const demoStatusName = (status, language) => ts(`demo_status_${status}`, language);
export const trialStatusName = (status, language) => ts(`trial_status_${status}`, language);

// ---- time ------------------------------------------------------------------------------------------------
// The API speaks UTC ISO timestamps with a timezone; an <input type="datetime-local"> speaks local wall-clock time with
// none. These two convert between them, so what a person types is exactly the instant the server stores.
const pad = (n) => String(n).padStart(2, '0');

export const toLocalInput = (iso) => {
  const d = iso ? new Date(iso) : new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const fromLocalInput = (value) => new Date(value).toISOString();

// A datetime-local value `ms` from now (the defaults of the "due" / "end" fields), rounded to the minute.
export const localInputIn = (ms) => toLocalInput(new Date(Date.now() + ms).toISOString());

export const HOUR = 3600 * 1000;
export const DAY = 24 * HOUR;

// "m:ss" (or "h:mm:ss") for a call's length; '-' when it was not recorded.
export const formatDuration = (seconds) => {
  if (seconds === null || seconds === undefined) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

// "in 2 days" / "3 hours ago" in the active language.
export const relativeTime = (iso, language) => {
  if (!iso) return '';
  const diff = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(language === 'ar' ? 'ar' : 'en', { numeric: 'auto' });
  if (abs < HOUR) return rtf.format(Math.round(diff / 60000), 'minute');
  if (abs < DAY) return rtf.format(Math.round(diff / HOUR), 'hour');
  return rtf.format(Math.round(diff / DAY), 'day');
};

// ---- the offer to move the lead along ----------------------------------------------------------------------
// After some activities the UI OFFERS the matching pipeline stage. It is an offer only: it appears when the SERVER
// lists that stage in the lead's `allowed_stages` (which already accounts for the caller's permission and the lead's
// state) and only when it is a step FORWARD; accepting it goes through the ordinary, server-validated stage action.
const RESULT_STAGE = { answered: 'contacted', call_back: 'contacted', interested: 'interested' };

export const suggestedStage = (kind, item, lead) => {
  const target = {
    call: RESULT_STAGE[item?.result],
    demo_scheduled: 'demo_scheduled',
    demo_completed: 'demo_completed',
    trial_started: 'trial',
  }[kind];
  if (!target || !lead || !(lead.allowed_stages || []).includes(target)) return null;
  return STAGES.indexOf(target) > STAGES.indexOf(lead.pipeline_stage) ? target : null;
};

export const suggestedStageLabel = (stage, language) => stageName(stage, language);

// ---- the people a follow-up / demo can be given to -------------------------------------------------------------
// The server decides who is eligible (an active staff member who can work this kind of item and see the lead). This
// only builds the short list a form offers: yourself (unless you are a Super Admin, who cannot receive work) and, for a
// caller who may assign, the lead's owner. Anything else the server would refuse with a clear message anyway.
export const assigneeOptions = (lead, me, canAssign, language) => {
  const options = [];
  const myId = me?.user?.id;
  if (myId && !me.is_super_admin) options.push({ value: myId, label: `${me.user.name} (${ts('act_you', language)})` });
  const owner = lead?.assigned_to;
  if (canAssign && owner && owner.id !== myId) options.push({ value: owner.id, label: owner.name });
  return options;
};

// Who a new item goes to by default: yourself; a Super Admin (who cannot receive work) gets the lead's owner.
export const defaultAssignee = (lead, me) => {
  if (me?.user?.id && !me.is_super_admin) return me.user.id;
  return lead?.assigned_to?.id || '';
};
