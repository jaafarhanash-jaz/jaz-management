import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import api from '@/utils/api';
import { validateAndFocus } from '@/utils/formValidation';
import { apiErrorMessage, fieldErrorsFrom } from '@/utils/salesErrors';
import { ts } from '@/utils/salesTranslations';

// The small building blocks shared by the Phase-3 dialogs (call, follow-up, demo, trial): one dialog frame with the
// validate-then-submit flow, and the form fields they have in common. Same look and behaviour as the Phase-2 forms.

export const FieldError = ({ id, message }) => (message
  ? <p className="text-xs text-red-600 mt-1" role="alert" data-testid={`${id}-error`}>{message}</p>
  : null);

const Required = () => <span className="text-red-600" aria-hidden="true"> *</span>;

export const DateTimeField = ({ id, label, value, onChange, required, error, hint }) => (
  <div>
    <Label htmlFor={id}>{label}{required && <Required />}</Label>
    <Input id={id} type="datetime-local" required={required} className={`mt-1 ${error ? 'border-red-500' : ''}`} value={value}
      onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} data-testid={id} dir="ltr" style={{ textAlign: 'start' }} />
    {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
    <FieldError id={id} message={error} />
  </div>
);

export const NotesField = ({ id, label, value, onChange, error, language, maxLength = 2000, rows = 3, optional = true }) => (
  <div>
    <Label htmlFor={id}>{label}{optional && <span className="text-gray-400 text-xs"> ({ts('field_optional', language)})</span>}</Label>
    <Textarea id={id} rows={rows} maxLength={maxLength} className={`mt-1 ${error ? 'border-red-500' : ''}`} value={value}
      onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} data-testid={id} dir="auto" />
    <FieldError id={id} message={error} />
  </div>
);

// Who a follow-up / demo is for. Hidden when there is nothing to choose (one option): the person is then implied.
export const AssigneeField = ({ id, value, onChange, options, error, language }) => {
  if (options.length === 0) return <p className="text-sm text-amber-700" data-testid={`${id}-none`}>{ts('act_assignee_none', language)}</p>;
  if (options.length === 1) return null;
  return (
    <div>
      <Label htmlFor={id}>{ts('act_assignee', language)}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="mt-1" data-testid={id}><SelectValue /></SelectTrigger>
        <SelectContent>{options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
      </Select>
      <p className="text-xs text-gray-500 mt-1">{ts('act_assignee_hint', language)}</p>
      <FieldError id={id} message={error} />
    </div>
  );
};

// A failed call -> { fields: {name: message}, general: message }. An error about one of THIS form's fields is shown under
// that field; anything else (an archived lead, a state conflict, a permission error) is shown once, above the buttons.
export const splitErrors = (err, language, formFields) => {
  const fields = {};
  Object.entries(fieldErrorsFrom(err, language)).forEach(([name, message]) => { if (formFields.includes(name)) fields[name] = message; });
  return { fields, general: Object.keys(fields).length ? '' : apiErrorMessage(err, language) };
};

// The dialog frame: title, optional description, the form (validated before it is submitted) and Cancel / Confirm.
export const ActivityFormDialog = ({
  open, onOpenChange, title, description, submitLabel, busy, error, onSubmit, children, testid, language, submitDisabled = false, destructive = false,
}) => (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent data-testid={testid} className="max-h-[90vh] overflow-y-auto">
      <DialogHeader className="text-start">
        <DialogTitle>{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <form noValidate className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (!validateAndFocus(e.currentTarget)) return; onSubmit(e); }}>
        {children}
        {error && <p className="text-sm text-red-600" role="alert" data-testid={`${testid}-error`}>{error}</p>}
        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" className="rounded-sm" onClick={() => onOpenChange(false)}>{ts('action_cancel', language)}</Button>
          <Button type="submit" disabled={busy || submitDisabled} data-testid={`${testid}-submit`}
            className={`${destructive ? 'bg-red-700 hover:bg-red-800' : 'bg-[#0033A0] hover:bg-[#002277]'} rounded-sm`}>
            {submitLabel}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
);

// The owner of a lead (for the assignee picker of a dialog opened from a cross-lead list, where only the lead's id is
// known). Fetched once, only while the dialog is open and only for a caller who may assign.
export const useLeadOwner = (leadId, enabled) => {
  const [owner, setOwner] = useState(null);
  useEffect(() => {
    if (!enabled || !leadId) return undefined;
    let cancelled = false;
    api.get(`/sales/leads/${leadId}`).then((res) => { if (!cancelled) setOwner(res.data.assigned_to || null); }).catch(() => {});
    return () => { cancelled = true; };
  }, [leadId, enabled]);
  return owner;
};
