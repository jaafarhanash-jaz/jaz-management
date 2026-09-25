// JAZ Sales - Phase 2 (leads): the vocabulary and the small formatting helpers shared by the Sales pages.
// The pipeline RULES live on the server (the lead payload carries `allowed_stages` and `can`); nothing here decides
// whether a move is allowed - it only lists, orders and labels things.
import { ts } from '@/utils/salesTranslations';

// Board / display order. Mirrors backend/sales/constants.py.
export const STAGES = ['new', 'assigned', 'contacted', 'interested', 'demo_scheduled', 'demo_completed', 'trial', 'negotiation', 'won', 'lost'];
export const PRIORITIES = ['low', 'medium', 'high'];
export const LOST_REASONS = [
  'too_expensive', 'not_interested', 'already_using_another_system', 'no_response', 'wrong_number',
  'business_closed', 'not_suitable', 'delayed_decision', 'competitor', 'other',
];
export const CAMPAIGN_STATUSES = ['active', 'paused', 'completed'];

// Badge colours. Every badge also carries its text label, so colour is never the only signal.
export const STAGE_STYLES = {
  new: 'bg-gray-50 text-gray-700 border-gray-200',
  assigned: 'bg-blue-50 text-blue-700 border-blue-200',
  contacted: 'bg-sky-50 text-sky-700 border-sky-200',
  interested: 'bg-violet-50 text-violet-700 border-violet-200',
  demo_scheduled: 'bg-amber-50 text-amber-800 border-amber-200',
  demo_completed: 'bg-orange-50 text-orange-800 border-orange-200',
  trial: 'bg-teal-50 text-teal-700 border-teal-200',
  negotiation: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200',
  won: 'bg-green-50 text-green-700 border-green-200',
  lost: 'bg-red-50 text-red-700 border-red-200',
};
export const PRIORITY_STYLES = {
  low: 'bg-gray-50 text-gray-700 border-gray-200',
  medium: 'bg-amber-50 text-amber-800 border-amber-200',
  high: 'bg-red-50 text-red-700 border-red-200',
};
export const CAMPAIGN_STATUS_STYLES = {
  active: 'bg-green-50 text-green-700 border-green-200',
  paused: 'bg-amber-50 text-amber-800 border-amber-200',
  completed: 'bg-gray-50 text-gray-700 border-gray-200',
};

export const stageName = (stage, language) => ts(`stage_${stage}`, language);
export const priorityName = (priority, language) => ts(`priority_${priority}`, language);
export const lostReasonName = (reason, language) => ts(`lost_${reason}`, language);
export const campaignStatusName = (status, language) => ts(`campaign_status_${status}`, language);

// Sources come from the server (fixed reference data, bilingual): look the key up in the loaded list.
export const sourceName = (sources, key, language) => {
  const source = (sources || []).find((s) => s.key === key);
  if (!source) return key || '-';
  return (language === 'ar' ? source.name_ar : source.name_en) || key;
};

const locale = (language) => (language === 'ar' ? 'ar-EG' : 'en-US');

export const formatDate = (iso, language) =>
  iso ? new Date(iso).toLocaleDateString(locale(language), { dateStyle: 'medium' }) : '-';

export const formatDateTime = (iso, language) =>
  iso ? new Date(iso).toLocaleString(locale(language), { dateStyle: 'medium', timeStyle: 'short' }) : '-';

// The lead value has no currency in the data model, so it is shown as a plain grouped number.
export const formatValue = (value, language) =>
  value === null || value === undefined ? '-' : Number(value).toLocaleString(locale(language), { maximumFractionDigits: 2 });

// "+964 770 123 4567" -> digits only, for tel: / wa.me links.
export const digitsOnly = (value) => (value || '').replace(/[^\d]/g, '');

// The lead form's fields, in the order the API and the timeline know them.
export const EDITABLE_FIELDS = [
  'business_name', 'business_type', 'description', 'contact_name', 'contact_position', 'phone', 'whatsapp', 'email',
  'website', 'country', 'city', 'address', 'latitude', 'longitude', 'source', 'campaign_id', 'priority',
  'estimated_value', 'notes',
];

// A lead as the form edits it: every field is a string ('' = no value), so inputs stay controlled.
export const leadToForm = (lead) => {
  const form = {};
  EDITABLE_FIELDS.forEach((field) => {
    const value = lead ? lead[field] : null;
    form[field] = value === null || value === undefined ? '' : String(value);
  });
  if (!lead) {
    form.source = 'manual_entry';
    form.priority = 'medium';
  }
  return form;
};

// The request body for a form: blank -> null (cleared), numbers as numbers.
export const formToPayload = (form, fields = EDITABLE_FIELDS) => {
  const payload = {};
  fields.forEach((field) => {
    const raw = typeof form[field] === 'string' ? form[field].trim() : form[field];
    if (field === 'source' || field === 'priority' || field === 'business_name') {
      payload[field] = raw;
    } else if (raw === '' || raw === undefined || raw === null) {
      payload[field] = null;
    } else if (field === 'latitude' || field === 'longitude' || field === 'estimated_value') {
      payload[field] = Number(raw);
    } else {
      payload[field] = raw;
    }
  });
  return payload;
};
