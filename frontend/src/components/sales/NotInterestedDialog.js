import { useEffect, useState } from 'react';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ActivityFormDialog, NotesField, splitErrors } from '@/components/sales/ActivityFields';
import api from '@/utils/api';
import { LOST_REASONS, lostReasonName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { toast } from 'sonner';

// "Not interested": the lead is marked LOST with a reason (and an optional note) through the ordinary pipeline endpoint
// (POST /leads/{id}/stage). Nothing is deleted - the lead and its whole timeline stay; the server decides whether the move
// is allowed.
const NotInterestedDialog = ({ open, onOpenChange, lead, language, onDone }) => {
  const [reason, setReason] = useState('not_interested');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) { setReason('not_interested'); setNote(''); setErrors({}); setGeneral(''); }
  }, [open]);

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    try {
      const body = { stage: 'lost', lost_reason: reason };
      if (note.trim()) body.note = note.trim();
      const res = await api.post(`/sales/leads/${lead.id}/stage`, body);
      toast.success(ts('wf_toast_not_interested', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, ['lost_reason', 'note']);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="not-interested-dialog" busy={busy} error={general}
      onSubmit={submit} title={ts('wf_ni_title', language)} description={ts('wf_ni_hint', language)}
      submitLabel={ts('wf_ni_submit', language)} destructive
    >
      <div>
        <Label htmlFor="ni-reason">{ts('wf_ni_reason', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
        <Select value={reason} onValueChange={setReason}>
          <SelectTrigger id="ni-reason" className="mt-1" data-testid="ni-reason"><SelectValue /></SelectTrigger>
          <SelectContent>
            {LOST_REASONS.map((r) => <SelectItem key={r} value={r}>{lostReasonName(r, language)}</SelectItem>)}
          </SelectContent>
        </Select>
        {errors.lost_reason && <p className="text-xs text-red-600 mt-1" role="alert">{errors.lost_reason}</p>}
      </div>
      <NotesField id="ni-note" label={ts('wf_ni_note', language)} value={note} onChange={setNote} error={errors.note} language={language} maxLength={1000} />
    </ActivityFormDialog>
  );
};

export default NotInterestedDialog;
