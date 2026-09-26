import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { Shuffle } from 'lucide-react';
import { toast } from 'sonner';

// The Sales Manager's lead distribution setting (GET / PUT /sales/settings/distribution, sales.settings.manage): automatic
// distribution ON/OFF and its one method, equal round-robin. The rotation - who receives new leads, in turn order, and whose
// turn is next - is the server's; manual assignment is never affected.
export const useDistributionStatus = (enabled) => {
  const [state, setState] = useState(null);
  const reload = () => api.get('/sales/settings/distribution').then((res) => setState(res.data)).catch(() => setState(false));
  useEffect(() => { if (enabled) reload(); }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  return [state, reload];
};

const DistributionDialog = ({ open, onOpenChange, language, onSaved }) => {
  const [data, setData] = useState(null);             // null = loading, false = failed
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setData(null);
    setError('');
    api.get('/sales/settings/distribution')
      .then((res) => { if (!cancelled) { setData(res.data); setEnabled(res.data.auto_distribution_enabled); } })
      .catch(() => { if (!cancelled) setData(false); });
    return () => { cancelled = true; };
  }, [open]);

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await api.put('/sales/settings/distribution', { auto_distribution_enabled: enabled, distribution_mode: 'equal' });
      setData(res.data);
      toast.success(ts('dist_saved', language));
      onSaved && onSaved(res.data);
      onOpenChange(false);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setBusy(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto" data-testid="distribution-dialog">
        <DialogHeader className="text-start pr-8">
          <DialogTitle>{ts('dist_title', language)}</DialogTitle>
          <DialogDescription>{ts('dist_hint', language)}</DialogDescription>
        </DialogHeader>

        {data === null && <p className="text-sm text-gray-500 py-4 text-center">{ts('loading', language)}</p>}
        {data === false && <p className="text-sm text-red-600" role="alert">{ts('dist_load_error', language)}</p>}

        {data && (
          <div className="space-y-5">
            <div className="flex items-center justify-between gap-4 rounded-md border border-gray-200 p-3">
              <Label htmlFor="dist-enabled" className="text-sm font-medium text-[#0A0A0A]">{ts('dist_enabled', language)}</Label>
              <Switch id="dist-enabled" checked={enabled} onCheckedChange={setEnabled} data-testid="dist-enabled" />
            </div>

            <fieldset>
              <legend className="text-sm font-medium text-[#0A0A0A] mb-2">{ts('dist_mode', language)}</legend>
              <div className="flex items-start gap-3 rounded-md border border-[#0033A0] bg-blue-50 p-3" data-testid="dist-mode-equal">
                <Shuffle className="w-4 h-4 mt-0.5 text-[#0033A0] shrink-0" aria-hidden="true" />
                <span>
                  <span className="block text-sm font-medium text-[#0A0A0A]">{ts('dist_mode_equal', language)}</span>
                  <span className="block text-xs text-gray-600 mt-0.5">{ts('dist_mode_equal_desc', language)}</span>
                </span>
              </div>
            </fieldset>

            <div>
              <p className="text-sm font-medium text-[#0A0A0A]">{ts('dist_rotation', language)}</p>
              <p className="text-xs text-gray-500 mt-0.5 mb-2">{ts('dist_rotation_hint', language)}</p>
              {data.rotation.length === 0 ? (
                <p className="text-sm text-amber-700" data-testid="dist-nobody">{ts('dist_nobody', language)}</p>
              ) : (
                <ol className="max-h-56 overflow-y-auto divide-y divide-gray-100 rounded-md border border-gray-200" data-testid="dist-rotation">
                  {data.rotation.map((member, i) => {
                    const next = enabled && data.next_assignee && data.next_assignee.id === member.id;
                    return (
                      <li key={member.id} className={`flex items-center justify-between gap-3 px-3 py-2 text-sm ${next ? 'bg-blue-50' : ''}`}>
                        <span className="min-w-0 truncate"><span className="text-gray-400 me-2">{i + 1}.</span><bdi>{member.name}</bdi></span>
                        <span className="flex items-center gap-2 shrink-0 text-xs text-gray-600">
                          {next && <span className="rounded-full bg-[#0033A0] text-white px-2 py-0.5" data-testid="dist-next">{ts('dist_next', language)}</span>}
                          {ts('dist_open', language).replace('{n}', member.open_leads)}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>

            <p className="text-xs text-gray-500">{ts('dist_manual_note', language)}</p>
            {data.updated_by && data.updated_at && (
              <p className="text-xs text-gray-500">{ts('dist_updated_by', language).replace('{name}', data.updated_by.name).replace('{date}', formatDateTime(data.updated_at, language))}</p>
            )}
            {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" className="rounded-sm" onClick={() => onOpenChange(false)}>{ts('action_cancel', language)}</Button>
          <Button type="button" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" disabled={busy || !data} onClick={save} data-testid="dist-save">
            {ts('dist_save', language)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default DistributionDialog;
