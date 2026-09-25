import { useEffect, useMemo, useState } from 'react';
import api from '@/utils/api';
import { DAY, assigneeOptions, defaultAssignee, fromLocalInput, localInputIn, toLocalInput } from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { ActivityFormDialog, AssigneeField, DateTimeField, NotesField, splitErrors, useLeadOwner } from '@/components/sales/ActivityFields';
import { toast } from 'sonner';

const FIELDS = ['scheduled_at', 'notes', 'assigned_to'];

// Schedule a demo for a lead (`lead`), or edit a scheduled one's notes / assignee (`demo`). Moving a demo in time is its
// own action (reschedule). There is no calendar integration: this is internal tracking.
const DemoDialog = ({ open, onOpenChange, lead, demo, language, onDone }) => {
  const editing = !!demo;
  const { can, me } = useSalesAccess();
  const canAssign = can('sales.leads.assign');
  const owner = useLeadOwner(demo?.lead?.id, open && editing && canAssign);
  const [form, setForm] = useState({ scheduled_at: '', notes: '', assigned_to: '' });
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  const options = useMemo(() => {
    if (!editing) return assigneeOptions(lead, me, canAssign, language);
    const list = assigneeOptions({ assigned_to: owner }, me, canAssign, language);
    const current = demo.assigned_to;
    if (canAssign && current && !list.some((o) => o.value === current.id)) list.push({ value: current.id, label: current.name });
    return canAssign ? list : [];
  }, [editing, lead, demo, owner, me, canAssign, language]);

  useEffect(() => {
    if (!open) return;
    setForm(demo
      ? { scheduled_at: toLocalInput(demo.scheduled_at), notes: demo.notes || '', assigned_to: demo.assigned_to.id }
      : { scheduled_at: localInputIn(2 * DAY), notes: '', assigned_to: defaultAssignee(lead, me) });
    setErrors({});
    setGeneral('');
  }, [open, demo?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    try {
      let res;
      if (editing) {
        const body = { notes: form.notes.trim() || null };
        if (canAssign && form.assigned_to && form.assigned_to !== demo.assigned_to.id) body.assigned_to = form.assigned_to;
        res = await api.patch(`/sales/demos/${demo.id}`, body);
      } else {
        res = await api.post('/sales/demos', {
          lead_id: lead.id, scheduled_at: fromLocalInput(form.scheduled_at), notes: form.notes.trim() || null,
          ...(form.assigned_to ? { assigned_to: form.assigned_to } : {}),
        });
      }
      toast.success(ts(editing ? 'toast_demo_updated' : 'toast_demo_scheduled', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, FIELDS);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  const nobody = !editing && options.length === 0;

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="demo-dialog" busy={busy} error={general} onSubmit={submit} submitDisabled={nobody}
      title={ts(editing ? 'demo_dialog_edit_title' : 'demo_dialog_create_title', language)}
      description={<>{lead?.business_name || demo?.lead?.business_name}<span className="block mt-1">{ts('demo_dialog_hint', language)}</span></>}
      submitLabel={ts('action_save', language)}
    >
      {!editing && <DateTimeField id="demo-when" label={ts('demo_field_when', language)} value={form.scheduled_at} onChange={(v) => set('scheduled_at', v)} required error={errors.scheduled_at} />}
      <NotesField id="demo-notes" label={ts('demo_field_notes', language)} value={form.notes} onChange={(v) => set('notes', v)} error={errors.notes} language={language} />
      <AssigneeField id="demo-assignee" value={form.assigned_to} onChange={(v) => set('assigned_to', v)} options={options} error={errors.assigned_to} language={language} />
    </ActivityFormDialog>
  );
};

export default DemoDialog;
