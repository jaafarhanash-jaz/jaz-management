import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { StageBadge } from '@/components/sales/LeadBadges';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { LOST_REASONS, lostReasonName, stageName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { toast } from 'sonner';

// Moves a lead along the pipeline. Which stages are offered is decided by the SERVER (`lead.allowed_stages` - the
// same rules the endpoint enforces); this dialog only presents them and collects the lost reason / note. A lead that
// ends up somewhere the server refuses is reported back as an error, never silently accepted.
const StageChangeDialog = ({ open, onOpenChange, lead, language, initialStage = null, onDone }) => {
  const [stage, setStage] = useState('');
  const [lostReason, setLostReason] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const allowed = lead?.allowed_stages || [];

  useEffect(() => {
    if (!open) return;
    setLostReason('');
    setNote('');
    setError('');
    if (initialStage && allowed.includes(initialStage)) setStage(initialStage);
    else setStage(allowed.length === 1 ? allowed[0] : '');
  }, [open, lead?.id, initialStage]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!lead) return null;

  const needsReason = stage === 'lost';
  const canSubmit = !!stage && (!needsReason || !!lostReason) && !busy;
  const closed = lead.pipeline_stage === 'won';

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError('');
    try {
      const body = { stage };
      if (needsReason) body.lost_reason = lostReason;
      if (note.trim()) body.note = note.trim();
      const res = await api.post(`/sales/leads/${lead.id}/stage`, body);
      toast.success(ts('toast_stage_updated', language));
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setBusy(false);
  };

  let hint = null;
  if (allowed.length === 0) hint = closed ? 'stage_won_terminal' : 'stage_no_moves';
  else if (lead.pipeline_stage === 'lost') hint = 'stage_reopen_hint';
  else if (!lead.assigned_to && allowed.length === 1 && allowed[0] === 'lost') hint = 'stage_assign_first_hint';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="stage-dialog" className="max-h-[90vh] overflow-y-auto">
        <DialogHeader className="text-start">
          <DialogTitle>{ts('stage_dialog_title', language)}</DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-2">
              <span>{lead.business_name}</span>
              <StageBadge stage={lead.pipeline_stage} language={language} />
            </div>
          </DialogDescription>
        </DialogHeader>

        {hint && <p className="text-sm text-gray-600 rounded-md bg-gray-50 border border-gray-200 p-3" data-testid="stage-hint">{ts(hint, language)}</p>}

        {allowed.length > 0 ? (
          <form onSubmit={submit} className="space-y-4" noValidate>
            <fieldset>
              <legend className="text-sm font-medium mb-2">{ts('stage_move_to', language)}</legend>
              <RadioGroup value={stage} onValueChange={setStage} className="grid grid-cols-1 sm:grid-cols-2 gap-2" aria-label={ts('stage_move_to', language)}>
                {allowed.map((s) => (
                  <label key={s} htmlFor={`stage-opt-${s}`} className={`flex items-center gap-3 rounded-md border p-3 cursor-pointer min-h-[44px] ${stage === s ? 'border-[#0033A0] bg-blue-50' : 'border-gray-200 hover:bg-gray-50'}`}>
                    <RadioGroupItem value={s} id={`stage-opt-${s}`} data-testid={`stage-option-${s}`} />
                    <span className="text-sm font-medium">{stageName(s, language)}</span>
                  </label>
                ))}
              </RadioGroup>
            </fieldset>

            {needsReason && (
              <div>
                <Label htmlFor="stage-lost-reason">{ts('stage_lost_reason', language)} <span className="text-red-600" aria-hidden="true">*</span></Label>
                <Select value={lostReason} onValueChange={setLostReason}>
                  <SelectTrigger id="stage-lost-reason" className="mt-1" data-testid="stage-lost-reason" aria-required="true">
                    <SelectValue placeholder={ts('stage_lost_reason_placeholder', language)} />
                  </SelectTrigger>
                  <SelectContent>
                    {LOST_REASONS.map((r) => <SelectItem key={r} value={r}>{lostReasonName(r, language)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div>
              <Label htmlFor="stage-note">{ts('stage_note', language)} <span className="text-gray-400 text-xs">({ts('field_optional', language)})</span></Label>
              <Textarea id="stage-note" className="mt-1" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} data-testid="stage-note" />
            </div>

            {error && <p className="text-sm text-red-600" role="alert" data-testid="stage-error">{error}</p>}

            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" className="rounded-sm" onClick={() => onOpenChange(false)}>{ts('action_cancel', language)}</Button>
              <Button type="submit" disabled={!canSubmit} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="stage-submit">
                {ts('stage_confirm', language)}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <DialogFooter>
            <Button type="button" variant="outline" className="rounded-sm" onClick={() => onOpenChange(false)}>{ts('action_close', language)}</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default StageChangeDialog;
