import { useEffect, useState } from 'react';
import api from '@/utils/api';
import { fromLocalInput, toLocalInput } from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';
import { ActivityFormDialog, DateTimeField, NotesField, splitErrors } from '@/components/sales/ActivityFields';
import { toast } from 'sonner';

// The one dialog behind every "end / change the state of an item" action: complete or cancel a follow-up, complete /
// cancel / no-show a demo, end or cancel a trial, reschedule a demo. It POSTs to `url`, optionally with a time field
// (`timeField`: { name, labelKey, required, initial, unchangedKey? } - `unchangedKey` is the message shown when the time was left
// as it is) and an optional note that lands on the lead's timeline.
const ActionNoteDialog = ({
  open, onOpenChange, url, title, description, confirmLabel, destructive = false, timeField = null, successKey, language, testid, onDone,
}) => {
  const [note, setNote] = useState('');
  const [time, setTime] = useState('');
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setNote('');
    setTime(timeField ? toLocalInput(timeField.initial) : '');
    setErrors({});
    setGeneral('');
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (timeField?.unchangedKey && time === toLocalInput(timeField.initial)) {   // the same minute the item already has: nothing to save
      setErrors({ [timeField.name]: ts(timeField.unchangedKey, language) });
      return;
    }
    setBusy(true);
    setErrors({});
    setGeneral('');
    const body = {};
    if (note.trim()) body.note = note.trim();
    if (timeField && time) body[timeField.name] = fromLocalInput(time);
    try {
      const res = await api.post(url, body);
      toast.success(ts(successKey, language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, [timeField?.name, 'note'].filter(Boolean));
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid={testid} busy={busy} error={general} onSubmit={submit}
      title={title} description={description} submitLabel={confirmLabel} destructive={destructive}
    >
      {timeField && (
        <DateTimeField id={`${testid}-time`} label={ts(timeField.labelKey, language)} value={time} onChange={setTime} required={!!timeField.required} error={errors[timeField.name]} />
      )}
      <NotesField id={`${testid}-note`} label={ts('act_note', language)} value={note} onChange={setNote} error={errors.note} language={language} maxLength={1000} />
    </ActivityFormDialog>
  );
};

export default ActionNoteDialog;
