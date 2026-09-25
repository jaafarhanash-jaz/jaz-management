// JAZ Sales - Phase 4 (conversion, customers, onboarding): the vocabulary and the small helpers shared by the Phase-4
// pages. As with the pipeline, the RULES live on the server: an onboarding record carries `allowed_stages` and `can`, a
// lead's conversion status carries `can_convert`, and every endpoint enforces the same rules regardless. Nothing here
// decides whether something is allowed - it lists, orders, labels and generates.
import { ts } from '@/utils/salesTranslations';

// Mirrors backend/sales/constants.py.
export const ONBOARDING_STAGES = ['won', 'assigned', 'contacted', 'setup_started', 'company_configured', 'employees_added', 'training', 'activated'];
export const CUSTOMER_STATUSES = ['active', 'onboarding', 'activated'];

// Badge colours. Every badge also carries its text label, so colour is never the only signal.
export const CUSTOMER_STATUS_STYLES = {
  active: 'bg-blue-50 text-blue-700 border-blue-200',
  onboarding: 'bg-amber-50 text-amber-800 border-amber-200',
  activated: 'bg-green-50 text-green-700 border-green-200',
};
export const ONBOARDING_STAGE_STYLES = {
  won: 'bg-gray-50 text-gray-700 border-gray-200',
  assigned: 'bg-blue-50 text-blue-700 border-blue-200',
  contacted: 'bg-sky-50 text-sky-700 border-sky-200',
  setup_started: 'bg-violet-50 text-violet-700 border-violet-200',
  company_configured: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200',
  employees_added: 'bg-orange-50 text-orange-800 border-orange-200',
  training: 'bg-teal-50 text-teal-700 border-teal-200',
  activated: 'bg-green-50 text-green-700 border-green-200',
};

export const customerStatusName = (status, language) => ts(`customer_status_${status}`, language);
export const onboardingStageName = (stage, language) => ts(`onb_stage_${stage}`, language);

// The initial password of a company owner, generated in the browser (crypto, never Math.random): 14 characters from an
// alphabet without look-alikes, always including a digit and a symbol. It is only ever shown to the person who is
// converting the lead, once; the server hashes it and Sales never stores it.
const LOWER = 'abcdefghijkmnpqrstuvwxyz';
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
const DIGITS = '23456789';
const SYMBOLS = '#$%&*+-=?@';
export const generatePassword = (length = 14) => {
  const pick = (chars) => chars[crypto.getRandomValues(new Uint32Array(1))[0] % chars.length];
  const all = LOWER + UPPER + DIGITS + SYMBOLS;
  const chars = [pick(LOWER), pick(UPPER), pick(DIGITS), pick(SYMBOLS)];
  while (chars.length < length) chars.push(pick(all));
  for (let i = chars.length - 1; i > 0; i -= 1) {           // Fisher-Yates, driven by the same source
    const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join('');
};

// The values the conversion form starts from (the server's preflight says what the lead holds) and the request for them.
export const FORM_FIELDS = ['business_name', 'owner_name', 'owner_email', 'owner_phone', 'address'];
export const valuesToForm = (values) => {
  const form = {};
  FORM_FIELDS.forEach((field) => { form[field] = values && values[field] ? String(values[field]) : ''; });
  return form;
};
// A preflight request: only what the person has typed (blank = "use the lead's value", which the server does itself).
export const formToPreflight = (form) => {
  const body = {};
  FORM_FIELDS.forEach((field) => { const v = (form[field] || '').trim(); if (v) body[field] = v; });
  return body;
};

// The price of a plan as shown in the picker: a plain grouped number (the data model has no currency).
export const planLabel = (plan, language) => {
  const price = Number(plan.price).toLocaleString(language === 'ar' ? 'ar-EG' : 'en-US', { maximumFractionDigits: 2 });
  return `${plan.name} · ${price} · ${ts('conv_plan_employees', language).replace('{n}', plan.max_employees)}`;
};
