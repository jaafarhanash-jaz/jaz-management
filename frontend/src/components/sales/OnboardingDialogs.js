import { useEffect, useState } from 'react';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { ActivityFormDialog, FieldError, NotesField, splitErrors } from '@/components/sales/ActivityFields';
import api from '@/utils/api';
import { ONBOARDING_STAGES, onboardingStageName } from '@/utils/salesCustomers';
import { apiErrorMessage } from '@/utils/salesErrors';
import { ts } from '@/utils/salesTranslations';
import { toast } from 'sonner';

// The three dialogs behind the onboarding actions - assign / reassign the onboarding employee, change the stage, keep the
// notes - and the hook that gives a page all three at once (`dialogs` rendered once, `assign` / `stage` / `notes` opening them
// on a record). The list page and the record's own page share every dialog and every request. What a dialog offers comes from
// the server (the assignee list, the record's `allowed_stages`); the endpoints enforce the same rules regardless.
const SEARCH_FROM = 6;   // a search box appears once the assignee list is long enough to need one

// ---- assign -------------------------------------------------------------------------------------------------
const AssignOnboardingDialog = ({ open, onOpenChange, item, language, onDone }) => {
  const [assignees, setAssignees] = useState(null);      // null = loading
  const [selected, setSelected] = useState('');
  const [filter, setFilter] = useState('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);
  const needle = filter.trim().toLowerCase();
  const visible = (assignees || []).filter((a) => !needle || `${a.name} ${a.email}`.toLowerCase().includes(needle));
  const chosen = visible.some((a) => a.id === selected) ? selected : '';     // what is submitted is always what is on screen

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setAssignees(null);
    setSelected('');
    setFilter('');
    setNote('');
    setErrors({});
    setGeneral('');
    api.get('/sales/onboarding/assignees')
      .then((res) => { if (!cancelled) setAssignees(res.data); })
      .catch((err) => { if (!cancelled) { setAssignees([]); setGeneral(apiErrorMessage(err, language)); } });
    return () => { cancelled = true; };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    if (!chosen) return;
    setBusy(true);
    setErrors({});
    setGeneral('');
    try {
      const body = { assigned_to: chosen };
      if (note.trim()) body.note = note.trim();
      const res = await api.post(`/sales/onboarding/${item.id}/assign`, body);
      toast.success(ts(item.assigned_to?.id === chosen ? 'toast_onb_assign_unchanged' : 'toast_onb_assigned', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, ['assigned_to', 'note']);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="onb-assign-dialog" busy={busy} error={general} onSubmit={submit}
      title={ts('onb_assign_title', language)} description={<>{item?.customer.business_name}<span className="block mt-1">{ts('onb_assign_hint', language)}</span></>}
      submitLabel={ts('onb_assign_confirm', language)} submitDisabled={!chosen}
    >
      {assignees === null ? (
        <p className="text-sm text-gray-500 py-4 text-center">{ts('loading', language)}</p>
      ) : assignees.length === 0 ? (
        <p className="text-sm text-gray-600 rounded-md bg-gray-50 border border-gray-200 p-3" data-testid="onb-assign-none">{ts('onb_assign_none', language)}</p>
      ) : (
        <>
          {assignees.length > SEARCH_FROM && (
            <div>
              <Label htmlFor="onb-assign-search" className="text-xs text-gray-600">{ts('assign_search', language)}</Label>
              <Input id="onb-assign-search" type="search" className="mt-1 h-10" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="onb-assign-search" />
            </div>
          )}
          {visible.length === 0 && <p className="text-sm text-gray-500 text-center py-3">{ts('assign_no_match', language)}</p>}
          <RadioGroup value={chosen} onValueChange={setSelected} className="relative gap-2 max-h-[36vh] overflow-y-auto p-1" aria-label={ts('assign_pick', language)} data-testid="onb-assign-list">
            {visible.map((a) => (
              <label key={a.id} htmlFor={`onb-assignee-${a.id}`} className={`flex items-center gap-3 rounded-md border p-3 cursor-pointer min-h-[44px] ${chosen === a.id ? 'border-[#0033A0] bg-blue-50' : 'border-gray-200 hover:bg-gray-50'}`}>
                <RadioGroupItem value={a.id} id={`onb-assignee-${a.id}`} data-testid={`onb-assignee-option-${a.id}`} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{a.name}{a.id === item?.assigned_to?.id && <span className="text-xs text-gray-500"> · {ts('onb_assign_current', language)}</span>}</span>
                  <span className="block text-xs text-gray-500" dir="ltr" style={{ textAlign: 'start' }}>{a.email}</span>
                </span>
                <span className="text-xs rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-gray-600 whitespace-nowrap">
                  {ts('onb_assign_open', language).replace('{n}', a.open_onboarding)}
                </span>
              </label>
            ))}
          </RadioGroup>
          <NotesField id="onb-assign-note" label={ts('onb_assign_note', language)} value={note} onChange={setNote} error={errors.note} language={language} maxLength={1000} rows={2} />
          <FieldError id="onb-assign-to" message={errors.assigned_to} />
        </>
      )}
    </ActivityFormDialog>
  );
};

// ---- stage --------------------------------------------------------------------------------------------------
// The stage after the current one when the server allows it, else the first stage it does allow.
const defaultStage = (item) => {
  const allowed = item?.allowed_stages || [];
  const next = ONBOARDING_STAGES[ONBOARDING_STAGES.indexOf(item?.stage) + 1];
  return allowed.includes(next) ? next : (allowed[0] || '');
};

const OnboardingStageDialog = ({ open, onOpenChange, item, language, onDone }) => {
  const [stage, setStage] = useState('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setStage(defaultStage(item));
    setNote('');
    setErrors({});
    setGeneral('');
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    try {
      const body = { stage };
      if (note.trim()) body.note = note.trim();
      const res = await api.post(`/sales/onboarding/${item.id}/stage`, body);
      toast.success(ts('toast_onb_stage', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, ['stage', 'note']);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="onb-stage-dialog" busy={busy} error={general} onSubmit={submit}
      title={ts('onb_stage_title', language)} description={<>{item?.customer.business_name}<span className="block mt-1">{ts('onb_stage_hint', language)}</span></>}
      submitLabel={ts('onb_stage_confirm', language)} submitDisabled={!stage}
    >
      <div>
        <Label htmlFor="onb-stage-select">{ts('onb_stage_pick', language)}</Label>
        <Select value={stage} onValueChange={setStage}>
          <SelectTrigger id="onb-stage-select" className="mt-1" data-testid="onb-stage-select"><SelectValue /></SelectTrigger>
          <SelectContent>
            {(item?.allowed_stages || []).map((s) => <SelectItem key={s} value={s} data-testid={`onb-stage-option-${s}`}>{onboardingStageName(s, language)}</SelectItem>)}
          </SelectContent>
        </Select>
        <FieldError id="onb-stage-select" message={errors.stage} />
      </div>
      <NotesField id="onb-stage-note" label={ts('onb_stage_note', language)} value={note} onChange={setNote} error={errors.note} language={language} maxLength={1000} rows={2} />
    </ActivityFormDialog>
  );
};

// ---- notes --------------------------------------------------------------------------------------------------
const OnboardingNotesDialog = ({ open, onOpenChange, item, language, onDone }) => {
  const [notes, setNotes] = useState('');
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setNotes(item?.notes || '');
    setErrors({});
    setGeneral('');
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    try {
      const res = await api.patch(`/sales/onboarding/${item.id}`, { notes: notes.trim() || null });
      toast.success(ts('toast_onb_notes', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const split = splitErrors(err, language, ['notes']);
      setErrors(split.fields);
      setGeneral(split.general);
    }
    setBusy(false);
  };

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="onb-notes-dialog" busy={busy} error={general} onSubmit={submit}
      title={ts('onb_notes_title', language)} description={<>{item?.customer.business_name}<span className="block mt-1">{ts('onb_notes_hint', language)}</span></>}
      submitLabel={ts('onb_notes_save', language)}
    >
      <NotesField id="onb-notes-text" label={ts('onb_notes_label', language)} value={notes} onChange={setNotes} error={errors.notes} language={language} maxLength={5000} rows={8} />
    </ActivityFormDialog>
  );
};

// ---- the hook -----------------------------------------------------------------------------------------------
const closeWith = (setter) => (open) => { if (!open) setter(null); };

// onChanged(eventType, record): called after the server accepted a change; `eventType` is the timeline event it produced
// ('onboarding_assigned', 'onboarding_stage_changed', 'onboarding_notes_updated'), `record` the updated onboarding.
export const useOnboardingActions = ({ language, onChanged }) => {
  const [assigning, setAssigning] = useState(null);
  const [staging, setStaging] = useState(null);
  const [noting, setNoting] = useState(null);
  const dialogs = (
    <>
      <AssignOnboardingDialog open={!!assigning} onOpenChange={closeWith(setAssigning)} item={assigning} language={language} onDone={(r) => onChanged('onboarding_assigned', r)} />
      <OnboardingStageDialog open={!!staging} onOpenChange={closeWith(setStaging)} item={staging} language={language} onDone={(r) => onChanged('onboarding_stage_changed', r)} />
      <OnboardingNotesDialog open={!!noting} onOpenChange={closeWith(setNoting)} item={noting} language={language} onDone={(r) => onChanged('onboarding_notes_updated', r)} />
    </>
  );
  return { dialogs, assign: setAssigning, stage: setStaging, notes: setNoting };
};
