import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { ts } from '@/utils/salesTranslations';
import { toast } from 'sonner';

// Assign (or reassign) one lead, or a whole selection, to a Sales Employee. The list comes from GET /sales/assignees:
// only ACTIVE staff whose ACTIVE role works assigned leads appear, each with their open workload - so a deactivated
// or revoked person is never offered (and the server would refuse them anyway).
const SEARCH_FROM = 6;   // a search box appears once the list is long enough to need one

const AssignDialog = ({ open, onOpenChange, leadIds, currentAssigneeId = null, language, onDone }) => {
  const [assignees, setAssignees] = useState(null);      // null = loading
  const [selected, setSelected] = useState('');
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const bulk = leadIds.length > 1;
  const needle = filter.trim().toLowerCase();
  const visible = (assignees || []).filter((a) => !needle || `${a.name} ${a.email}`.toLowerCase().includes(needle));
  // a choice that the search has hidden does not count: what is submitted is always what is on screen
  const chosen = visible.some((a) => a.id === selected) ? selected : '';

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setAssignees(null);
    setSelected('');
    setFilter('');
    setError('');
    api.get('/sales/assignees')
      .then((res) => { if (!cancelled) setAssignees(res.data); })
      .catch((err) => { if (!cancelled) { setAssignees([]); setError(apiErrorMessage(err, language)); } });
    return () => { cancelled = true; };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e) => {
    e.preventDefault();
    if (!chosen || busy) return;
    setBusy(true);
    setError('');
    try {
      if (bulk) {
        const res = await api.post('/sales/leads/bulk-assign', { lead_ids: leadIds, assigned_to: chosen });
        toast.success(ts('toast_bulk_assigned', language).replace('{n}', res.data.assigned).replace('{u}', res.data.unchanged));
        onDone && onDone(res.data);
      } else {
        const res = await api.post(`/sales/leads/${leadIds[0]}/assign`, { assigned_to: chosen });
        toast.success(ts(res.data.changed ? 'toast_assigned' : 'toast_assign_unchanged', language));
        onDone && onDone(res.data.lead);
      }
      onOpenChange(false);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setBusy(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="assign-dialog" className="max-h-[90vh] overflow-y-auto">
        <DialogHeader className="text-start">
          <DialogTitle>{ts(bulk ? 'assign_title_bulk' : 'assign_title', language).replace('{n}', leadIds.length)}</DialogTitle>
          <DialogDescription>{ts('assign_hint', language)}</DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4" noValidate>
          {assignees === null ? (
            <p className="text-sm text-gray-500 py-4 text-center">{ts('loading', language)}</p>
          ) : assignees.length === 0 ? (
            <p className="text-sm text-gray-600 rounded-md bg-gray-50 border border-gray-200 p-3" data-testid="assign-none">{ts('assign_none', language)}</p>
          ) : (
            <>
              {assignees.length > SEARCH_FROM && (
                <div>
                  <Label htmlFor="assign-search" className="text-xs text-gray-600">{ts('assign_search', language)}</Label>
                  <Input id="assign-search" type="search" className="mt-1 h-10" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="assign-search" />
                </div>
              )}
              {visible.length === 0 && <p className="text-sm text-gray-500 text-center py-3" data-testid="assign-no-match">{ts('assign_no_match', language)}</p>}
              {/* the list scrolls on its own so the search box and the Cancel/Assign buttons never leave the screen */}
              <RadioGroup value={chosen} onValueChange={setSelected} className="relative gap-2 max-h-[40vh] overflow-y-auto p-1" aria-label={ts('assign_pick', language)} data-testid="assign-list">
                {visible.map((a) => (
                  <label key={a.id} htmlFor={`assignee-${a.id}`} className={`flex items-center gap-3 rounded-md border p-3 cursor-pointer min-h-[44px] ${chosen === a.id ? 'border-[#0033A0] bg-blue-50' : 'border-gray-200 hover:bg-gray-50'}`}>
                    <RadioGroupItem value={a.id} id={`assignee-${a.id}`} data-testid={`assignee-option-${a.id}`} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">{a.name}{a.id === currentAssigneeId && <span className="text-xs text-gray-500"> · {ts('assign_current', language)}</span>}</span>
                      <span className="block text-xs text-gray-500" dir="ltr" style={{ textAlign: 'start' }}>{a.email}</span>
                    </span>
                    <span className="text-xs rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-gray-600 whitespace-nowrap">
                      {ts('assign_open_leads', language).replace('{n}', a.open_leads)}
                    </span>
                  </label>
                ))}
              </RadioGroup>
            </>
          )}

          {error && <p className="text-sm text-red-600" role="alert" data-testid="assign-error">{error}</p>}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" className="rounded-sm" onClick={() => onOpenChange(false)}>{ts('action_cancel', language)}</Button>
            <Button type="submit" disabled={!chosen || busy} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="assign-submit">
              {ts('assign_confirm', language)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default AssignDialog;
