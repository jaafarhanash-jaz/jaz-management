// Turning a failed Sales API call into something a person can act on.
//
// The API answers with one of three shapes: {detail: {field, message, code?, ...extra}} for business-rule and validation
// errors, {detail: "text"} (e.g. 403 "Access denied"), and FastAPI's 422 {detail: [{loc, msg}, ...]}. Known `code`s are
// shown in the user's language; anything else falls back to the server's message.
import { ts } from '@/utils/salesTranslations';

const detailOf = (err) => err?.response?.data?.detail;

// { status, code, field, message, extra } - `extra` holds structured payloads (duplicates, lead_count, missing, ...).
export const parseApiError = (err) => {
  const status = err?.response?.status || 0;
  const detail = detailOf(err);
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const { field, message, code, ...extra } = detail;
    return { status, code: code || null, field: field || null, message: message || null, extra };
  }
  if (Array.isArray(detail)) {
    const first = detail[0] || {};
    return { status, code: 'validation', field: (first.loc || []).slice(-1)[0] || null, message: first.msg || null, extra: { items: detail } };
  }
  return { status, code: null, field: null, message: typeof detail === 'string' ? detail : null, extra: {} };
};

// Field -> message for a form. 400/409 carry one field; a 422 carries one entry per invalid input.
export const fieldErrorsFrom = (err, language) => {
  const parsed = parseApiError(err);
  if (parsed.code === 'validation') {
    const out = {};
    (parsed.extra.items || []).forEach((item) => {
      const name = (item.loc || []).slice(-1)[0];
      if (name && !out[name]) out[name] = item.msg;
    });
    return out;
  }
  if (parsed.field && parsed.field !== 'duplicates') return { [parsed.field]: friendlyMessage(parsed, language) };
  return {};
};

// The message to toast / show for an error.
export const friendlyMessage = (parsed, language) => {
  if (parsed.code && parsed.code !== 'validation') {
    const key = `err_${parsed.code}`;
    const localized = ts(key, language);
    if (localized !== key) {
      return localized.replace('{n}', parsed.extra?.lead_count ?? parsed.extra?.missing?.length ?? parsed.extra?.archived?.length ?? '');
    }
  }
  if (parsed.status === 403) return ts('err_forbidden', language);
  if (parsed.status === 404) return ts('err_not_found', language);
  return parsed.message || ts('toast_error', language);
};

export const apiErrorMessage = (err, language) => friendlyMessage(parseApiError(err), language);
