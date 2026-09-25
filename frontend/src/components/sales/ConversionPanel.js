import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { CustomerStatusBadge } from '@/components/sales/CustomerBadges';
import ConvertLeadDialog from '@/components/sales/ConvertLeadDialog';
import { Handshake } from 'lucide-react';

// "Convert to customer", on the page of a WON lead: where the lead stands (GET /leads/{id}/conversion) - converted (and to
// which customer), ready for the caller to convert, or why not. Shown only to a caller who may view customers, and only for a
// won lead. Every decision comes from the server (`can_convert`, `blocker`); this panel never decides anything itself.
const ConversionPanel = ({ lead, language, onConverted }) => {
  const { can } = useSalesAccess();
  const [status, setStatus] = useState(null);           // null = loading, false = could not be loaded
  const [open, setOpen] = useState(false);
  const show = lead.pipeline_stage === 'won' && can('sales.customers.view');

  const load = useCallback(async () => {
    try {
      const res = await api.get(`/sales/leads/${lead.id}/conversion`);
      setStatus(res.data);
    } catch (err) {
      setStatus(false);
    }
  }, [lead.id]);

  useEffect(() => { if (show) load(); }, [show, load, lead.archived_at]);

  if (!show || status === null || status === false) return null;

  const converted = status.converted ? status.customer : null;
  const done = (result) => { load(); onConverted && onConverted(result); };

  return (
    <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="conversion-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-[#0A0A0A] flex items-center gap-2"><Handshake className="w-5 h-5 text-[#0033A0]" aria-hidden="true" />{ts('conv_panel_title', language)}</h2>
          {converted ? (
            <div className="mt-2 space-y-1" data-testid="conversion-converted">
              <p className="text-sm text-gray-700">{ts('conv_panel_converted', language)}</p>
              <p className="text-sm flex flex-wrap items-center gap-2">
                <CustomerStatusBadge status={converted.status} language={language} />
                <bdi className="font-medium text-[#0A0A0A]">{converted.company.name}</bdi>
              </p>
              <p className="text-xs text-gray-500">
                {ts('cust_converted_by', language).replace('{name}', converted.converted_by?.name || '-')} · <bdi>{formatDateTime(converted.converted_at, language)}</bdi>
              </p>
            </div>
          ) : status.can_convert ? (
            <p className="text-sm text-gray-700 mt-2" data-testid="conversion-ready">{ts('conv_panel_ready', language)}</p>
          ) : status.blocker === 'lead_archived' ? (
            <p className="text-sm text-gray-600 mt-2" data-testid="conversion-blocked">{ts('conv_panel_archived', language)}</p>
          ) : (
            <p className="text-sm text-gray-600 mt-2" data-testid="conversion-wait">{ts('conv_panel_wait', language)}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {converted && (
            <Button asChild variant="outline" className="rounded-sm">
              <Link to={`/sales/customers/${converted.id}`} data-testid="conversion-view-customer">{ts('conv_panel_view', language)}</Link>
            </Button>
          )}
          {!converted && status.can_convert && (
            <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => setOpen(true)} data-testid="convert-btn">
              <Handshake className="w-4 h-4 me-2" aria-hidden="true" />{ts('conv_action_convert', language)}
            </Button>
          )}
        </div>
      </div>
      <ConvertLeadDialog open={open} onOpenChange={setOpen} lead={lead} language={language} onDone={done} />
    </Card>
  );
};

export default ConversionPanel;
