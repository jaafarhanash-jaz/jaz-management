import { useEffect, useState } from 'react';
import api from '@/utils/api';
import { DAY, fromLocalInput, localInputIn, toLocalInput } from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';
import { ActivityFormDialog, DateTimeField, NotesField, splitErrors } from '@/components/sales/ActivityFields';
import { toast } from 'sonner';

const FIELDS = ['started_at', 'expected_end_at', 'notes'];
const DEFAULT_TRIAL_DAYS = 14;

// Start a Sales-side trial on a lead (`lead`), or edit an active one's expected end / notes (`trial`). This is tracking
// only: it never creates or changes a subscription. A lead can have one active trial (the server answers 409 otherwise).
const TrialDialog = ({ open, onOpenChange, lead, trial, language, onDone }) => {
  const editing = !!trial;
  const [form, setForm] = useState({ started_at: '', expected_end_at: '', notes: '' });
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(trial
      ? { started_at: toLocalInput(trial.started_at), expected_end_at: toLocalInput(trial.expected_end_at), notes: trial.notes || '' }
      : { started_at: localInputIn(0), expected_end_at: localInputIn(DEFAULT_TRIAL_DAYS * DAY), notes: '' });
    setErrors({});
    setGeneral('');
  }, [open, trial?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    const body = { expected_end_at: fromLocalInput(form.expected_end_at), notes: form.notes.trim() || null };
    try {
      const res = editing
        ? await api.patch(`/sales/trials/${trial.id}`, body)
        : await api.post('/sales/trials', { ...body, lead_id: lead.id, started_at: fromLocalInput(form.started_at) });
      toast.success(ts(editing ? 'toast_trial_updated' : 'toast_trial_started', language));
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
      open={open} onOpenChange={onOpenChange} language={language} testid="trial-dialog" busy={busy} error={general} onSubmit={submit}
      title={ts(editing ? 'trial_dialog_edit_title' : 'trial_dialog_start_title', language)}
      description={<>{lead?.business_name || trial?.lead?.business_name}<span className="block mt-1">{ts('trial_dialog_hint', language)}</span></>}
      submitLabel={ts('action_save', language)}
    >
      {!editing && <DateTimeField id="trial-started" label={ts('trial_field_started', language)} value={form.started_at} onChange={(v) => set('started_at', v)} required error={errors.started_at} />}
      <DateTimeField id="trial-expected-end" label={ts('trial_field_expected_end', language)} value={form.expected_end_at} onChange={(v) => set('expected_end_at', v)} required error={errors.expected_end_at} />
      <NotesField id="trial-notes" label={ts('trial_field_notes', language)} value={form.notes} onChange={(v) => set('notes', v)} error={errors.notes} language={language} />
    </ActivityFormDialog>
  );
};

export default TrialDialog;
