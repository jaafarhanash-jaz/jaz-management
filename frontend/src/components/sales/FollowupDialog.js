import { useEffect, useMemo, useState } from 'react';
import api from '@/utils/api';
import { DAY, assigneeOptions, defaultAssignee, fromLocalInput, localInputIn, toLocalInput } from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { ActivityFormDialog, AssigneeField, DateTimeField, NotesField, splitErrors, useLeadOwner } from '@/components/sales/ActivityFields';
import { toast } from 'sonner';

const FIELDS = ['due_at', 'notes', 'assigned_to'];

// Add a follow-up to a lead (`lead`), or edit a pending one (`followup`). It is for the caller unless the caller may
// assign work to others (sales.leads.assign) and picks somebody else - the server checks that person is eligible.
const FollowupDialog = ({ open, onOpenChange, lead, followup, language, onDone }) => {
  const editing = !!followup;
  const { can, me } = useSalesAccess();
  const canAssign = can('sales.leads.assign');
  const owner = useLeadOwner(followup?.lead?.id, open && editing && canAssign);
  const [form, setForm] = useState({ due_at: '', notes: '', assigned_to: '' });
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  const options = useMemo(() => {
    if (!editing) return assigneeOptions(lead, me, canAssign, language);
    const list = assigneeOptions({ assigned_to: owner }, me, canAssign, language);
    const current = followup.assigned_to;
    if (canAssign && current && !list.some((o) => o.value === current.id)) list.push({ value: current.id, label: current.name });
    return canAssign ? list : [];
  }, [editing, lead, followup, owner, me, canAssign, language]);

  useEffect(() => {
    if (!open) return;
    setForm(followup
      ? { due_at: toLocalInput(followup.due_at), notes: followup.notes || '', assigned_to: followup.assigned_to.id }
      : { due_at: localInputIn(DAY), notes: '', assigned_to: defaultAssignee(lead, me) });
    setErrors({});
    setGeneral('');
  }, [open, followup?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    const body = { due_at: fromLocalInput(form.due_at), notes: form.notes.trim() || null };
    try {
      let res;
      if (editing) {
        if (canAssign && form.assigned_to && form.assigned_to !== followup.assigned_to.id) body.assigned_to = form.assigned_to;
        res = await api.patch(`/sales/followups/${followup.id}`, body);
      } else {
        res = await api.post('/sales/followups', { ...body, lead_id: lead.id, ...(form.assigned_to ? { assigned_to: form.assigned_to } : {}) });
      }
      toast.success(ts(editing ? 'toast_followup_updated' : 'toast_followup_created', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, FIELDS);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  // nobody can receive it (e.g. a Super Admin on an unassigned lead): say so instead of offering a doomed form
  const nobody = !editing && options.length === 0;

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="followup-dialog" busy={busy} error={general} onSubmit={submit} submitDisabled={nobody}
      title={ts(editing ? 'followup_dialog_edit_title' : 'followup_dialog_create_title', language)}
      description={lead?.business_name || followup?.lead?.business_name}
      submitLabel={ts('action_save', language)}
    >
      <DateTimeField id="followup-due" label={ts('followup_field_due', language)} value={form.due_at} onChange={(v) => set('due_at', v)} required error={errors.due_at} />
      <NotesField id="followup-notes" label={ts('followup_field_notes', language)} value={form.notes} onChange={(v) => set('notes', v)} error={errors.notes} language={language} />
      <AssigneeField id="followup-assignee" value={form.assigned_to} onChange={(v) => set('assigned_to', v)} options={options} error={errors.assigned_to} language={language} />
    </ActivityFormDialog>
  );
};

export default FollowupDialog;
