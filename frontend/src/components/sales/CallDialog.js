import { useEffect, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { CALL_RESULTS, callResultName, fromLocalInput, localInputIn, toLocalInput } from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';
import { ActivityFormDialog, DateTimeField, FieldError, NotesField, splitErrors } from '@/components/sales/ActivityFields';
import { toast } from 'sonner';

const FIELDS = ['result', 'called_at', 'duration_seconds', 'notes'];
const EMPTY = { result: '', called_at: '', minutes: '', seconds: '', notes: '' };

// Log a call to a lead (`lead`), or correct one (`call`). A call records something that already happened, so its time
// defaults to now and cannot be in the future (the server refuses it). Logging or correcting a call never moves the lead.
const CallDialog = ({ open, onOpenChange, lead, call, language, onDone }) => {
  const editing = !!call;
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (call) {
      const d = call.duration_seconds;
      setForm({
        result: call.result, called_at: toLocalInput(call.called_at), minutes: d === null ? '' : String(Math.floor(d / 60)),
        seconds: d === null ? '' : String(d % 60), notes: call.notes || '',
      });
    } else {
      setForm({ ...EMPTY, called_at: localInputIn(0) });
    }
    setErrors({});
    setGeneral('');
  }, [open, call?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };

  const submit = async () => {
    if (!form.result) { setErrors({ result: ts('act_required', language) }); return; }
    setBusy(true);
    setErrors({});
    setGeneral('');
    const blank = form.minutes === '' && form.seconds === '';
    const body = {
      result: form.result,
      called_at: fromLocalInput(form.called_at),
      duration_seconds: blank ? null : (parseInt(form.minutes || '0', 10) * 60) + parseInt(form.seconds || '0', 10),
      notes: form.notes.trim() || null,
    };
    if (!editing) body.lead_id = lead.id;
    try {
      const res = editing ? await api.patch(`/sales/calls/${call.id}`, body) : await api.post('/sales/calls', body);
      toast.success(ts(editing ? 'toast_call_updated' : 'toast_call_created', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, FIELDS);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="call-dialog" busy={busy} error={general} onSubmit={submit}
      title={ts(editing ? 'call_dialog_edit_title' : 'call_dialog_create_title', language)}
      description={<>{lead?.business_name || call?.lead?.business_name}<span className="block mt-1">{ts('call_dialog_hint', language)}</span></>}
      submitLabel={ts('action_save', language)}
    >
      <div>
        <Label htmlFor="call-result">{ts('call_field_result', language)} <span className="text-red-600" aria-hidden="true">*</span></Label>
        <Select value={form.result} onValueChange={(v) => set('result', v)}>
          <SelectTrigger id="call-result" className={`mt-1 ${errors.result ? 'border-red-500' : ''}`} data-testid="call-result" aria-required="true">
            <SelectValue placeholder={ts('call_field_result_placeholder', language)} />
          </SelectTrigger>
          <SelectContent>{CALL_RESULTS.map((r) => <SelectItem key={r} value={r}>{callResultName(r, language)}</SelectItem>)}</SelectContent>
        </Select>
        <FieldError id="call-result" message={errors.result} />
      </div>
      <DateTimeField id="call-called-at" label={ts('call_field_called_at', language)} value={form.called_at} onChange={(v) => set('called_at', v)} required error={errors.called_at} />
      <div>
        <Label htmlFor="call-minutes">{ts('call_field_duration', language)} <span className="text-gray-400 text-xs">({ts('field_optional', language)})</span></Label>
        <div className="flex items-center gap-2 mt-1">
          <Input id="call-minutes" type="number" inputMode="numeric" min="0" max="1440" step="1" className="w-24" value={form.minutes}
            onChange={(e) => set('minutes', e.target.value)} aria-label={ts('call_field_minutes', language)} data-testid="call-minutes" dir="ltr" style={{ textAlign: 'start' }} />
          <span className="text-sm text-gray-600">{ts('call_field_minutes', language)}</span>
          <Input id="call-seconds" type="number" inputMode="numeric" min="0" max="59" step="1" className="w-24" value={form.seconds}
            onChange={(e) => set('seconds', e.target.value)} aria-label={ts('call_field_seconds', language)} data-testid="call-seconds" dir="ltr" style={{ textAlign: 'start' }} />
          <span className="text-sm text-gray-600">{ts('call_field_seconds', language)}</span>
        </div>
        <FieldError id="call-duration" message={errors.duration_seconds} />
      </div>
      <NotesField id="call-notes" label={ts('call_field_notes', language)} value={form.notes} onChange={(v) => set('notes', v)} error={errors.notes} language={language} />
    </ActivityFormDialog>
  );
};

export default CallDialog;
