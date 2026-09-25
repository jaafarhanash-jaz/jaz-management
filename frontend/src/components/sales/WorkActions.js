import { useState } from 'react';
import ActionNoteDialog from '@/components/sales/ActionNoteDialog';
import CallDialog from '@/components/sales/CallDialog';
import DemoDialog from '@/components/sales/DemoDialog';
import FollowupDialog from '@/components/sales/FollowupDialog';
import TrialDialog from '@/components/sales/TrialDialog';
import { ts } from '@/utils/salesTranslations';

// One hook per kind of work item. Each returns the dialogs it needs (render `dialogs` once, anywhere in the page) and the
// handlers that open them (create / edit / complete / cancel ...). A page calls a hook, renders `dialogs`, and wires the
// handlers to its buttons - so the lead page and the four list pages share every dialog and every request.
//
//   lead      the lead a NEW item is for (only the lead page creates items; a list page has no `create`)
//   onChanged(eventType, item)   called after the server accepted a change; `eventType` is the timeline event it
//             produced ('call_created', 'demo_completed', ...) so the caller can refresh and, where it makes sense,
//             OFFER the matching pipeline stage. Nothing here ever moves a lead by itself.
const closeWith = (setter) => (open) => { if (!open) setter(null); };

export const useCallActions = ({ language, lead = null, onChanged }) => {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const dialogs = (
    <>
      <CallDialog open={creating} onOpenChange={setCreating} lead={lead} language={language} onDone={(item) => onChanged('call_created', item)} />
      <CallDialog open={!!editing} onOpenChange={closeWith(setEditing)} call={editing} language={language} onDone={(item) => onChanged('call_updated', item)} />
    </>
  );
  return { dialogs, create: () => setCreating(true), edit: setEditing };
};

export const useFollowupActions = ({ language, lead = null, onChanged }) => {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [ending, setEnding] = useState(null);                      // { item, action: 'complete' | 'cancel' }
  const cancel = ending?.action === 'cancel';
  const dialogs = (
    <>
      <FollowupDialog open={creating} onOpenChange={setCreating} lead={lead} language={language} onDone={(item) => onChanged('followup_created', item)} />
      <FollowupDialog open={!!editing} onOpenChange={closeWith(setEditing)} followup={editing} language={language} onDone={(item) => onChanged('followup_updated', item)} />
      <ActionNoteDialog
        open={!!ending} onOpenChange={closeWith(setEnding)} language={language} testid="followup-end-dialog" destructive={cancel}
        url={ending ? `/sales/followups/${ending.item.id}/${ending.action}` : ''}
        title={ts(cancel ? 'followup_cancel_title' : 'followup_complete_title', language)}
        description={<>{ending?.item.lead.business_name}<span className="block mt-1">{ts(cancel ? 'followup_cancel_hint' : 'followup_complete_hint', language)}</span></>}
        confirmLabel={ts(cancel ? 'followup_action_cancel' : 'followup_action_complete', language)}
        successKey={cancel ? 'toast_followup_cancelled' : 'toast_followup_completed'}
        onDone={(item) => onChanged(cancel ? 'followup_cancelled' : 'followup_completed', item)}
      />
    </>
  );
  return {
    dialogs, create: () => setCreating(true), edit: setEditing,
    complete: (item) => setEnding({ item, action: 'complete' }), cancel: (item) => setEnding({ item, action: 'cancel' }),
  };
};

// action -> what the ActionNoteDialog says and which timeline event the change produces
const DEMO_ACTIONS = {
  reschedule: { title: 'demo_reschedule_title', hint: 'demo_reschedule_hint', confirm: 'demo_action_reschedule', toast: 'toast_demo_rescheduled', event: 'demo_rescheduled' },
  complete: { title: 'demo_complete_title', hint: 'demo_end_hint', confirm: 'demo_action_complete', toast: 'toast_demo_completed', event: 'demo_completed' },
  cancel: { title: 'demo_cancel_title', hint: 'demo_end_hint', confirm: 'demo_action_cancel', toast: 'toast_demo_cancelled', event: 'demo_cancelled', destructive: true },
  'no-show': { title: 'demo_no_show_title', hint: 'demo_end_hint', confirm: 'demo_action_no_show', toast: 'toast_demo_no_show', event: 'demo_no_show', destructive: true },
};

export const useDemoActions = ({ language, lead = null, onChanged }) => {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [ending, setEnding] = useState(null);                      // { item, action: 'reschedule' | 'complete' | 'cancel' | 'no-show' }
  const config = ending ? DEMO_ACTIONS[ending.action] : null;
  const dialogs = (
    <>
      <DemoDialog open={creating} onOpenChange={setCreating} lead={lead} language={language} onDone={(item) => onChanged('demo_scheduled', item)} />
      <DemoDialog open={!!editing} onOpenChange={closeWith(setEditing)} demo={editing} language={language} onDone={(item) => onChanged('demo_updated', item)} />
      <ActionNoteDialog
        open={!!ending} onOpenChange={closeWith(setEnding)} language={language} testid="demo-end-dialog" destructive={!!config?.destructive}
        url={ending ? `/sales/demos/${ending.item.id}/${ending.action}` : ''}
        timeField={ending?.action === 'reschedule' ? { name: 'scheduled_at', labelKey: 'demo_field_when', required: true, initial: ending.item.scheduled_at, unchangedKey: 'err_demo_same_time' } : null}
        title={config ? ts(config.title, language) : ''}
        description={config && <>{ending.item.lead.business_name}<span className="block mt-1">{ts(config.hint, language)}</span></>}
        confirmLabel={config ? ts(config.confirm, language) : ''}
        successKey={config ? config.toast : 'toast_demo_updated'}
        onDone={(item) => onChanged(config.event, item)}
      />
    </>
  );
  return {
    dialogs, create: () => setCreating(true), edit: setEditing,
    reschedule: (item) => setEnding({ item, action: 'reschedule' }), complete: (item) => setEnding({ item, action: 'complete' }),
    cancel: (item) => setEnding({ item, action: 'cancel' }), noShow: (item) => setEnding({ item, action: 'no-show' }),
  };
};

export const useTrialActions = ({ language, lead = null, onChanged }) => {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [ending, setEnding] = useState(null);                      // { item, action: 'complete' | 'cancel' }
  const cancel = ending?.action === 'cancel';
  const dialogs = (
    <>
      <TrialDialog open={creating} onOpenChange={setCreating} lead={lead} language={language} onDone={(item) => onChanged('trial_started', item)} />
      <TrialDialog open={!!editing} onOpenChange={closeWith(setEditing)} trial={editing} language={language} onDone={(item) => onChanged('trial_updated', item)} />
      <ActionNoteDialog
        open={!!ending} onOpenChange={closeWith(setEnding)} language={language} testid="trial-end-dialog" destructive={cancel}
        url={ending ? `/sales/trials/${ending.item.id}/${ending.action}` : ''}
        timeField={ending?.action === 'complete' ? { name: 'actual_end_at', labelKey: 'trial_field_actual_end', required: false, initial: new Date().toISOString() } : null}
        title={ts(cancel ? 'trial_cancel_title' : 'trial_complete_title', language)}
        description={<>{ending?.item.lead.business_name}<span className="block mt-1">{ts(cancel ? 'trial_cancel_hint' : 'trial_complete_hint', language)}</span></>}
        confirmLabel={ts(cancel ? 'trial_action_cancel' : 'trial_action_complete', language)}
        successKey={cancel ? 'toast_trial_cancelled' : 'toast_trial_completed'}
        onDone={(item) => onChanged(cancel ? 'trial_cancelled' : 'trial_completed', item)}
      />
    </>
  );
  return {
    dialogs, create: () => setCreating(true), edit: setEditing,
    complete: (item) => setEnding({ item, action: 'complete' }), cancel: (item) => setEnding({ item, action: 'cancel' }),
  };
};
