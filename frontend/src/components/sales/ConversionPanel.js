import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { CustomerStatusBadge } from '@/components/sales/CustomerBadges';
import CustomerSetupWizard from '@/components/sales/CustomerSetupWizard';
import NotInterestedDialog from '@/components/sales/NotInterestedDialog';
import { Handshake, ThumbsDown } from 'lucide-react';

// The customer side of a lead, on its page: where the lead stands (GET /leads/{id}/conversion) - converted (and to which
// customer), ready for the Customer Setup, or why not. Simplified workflow: the setup starts from a WON lead or from an OPEN,
// assigned one (the salesperson's "Agreed" wins it), so for an open lead the panel also offers "Not interested". Every decision
// comes from the server (`can_setup`, `setup_blocker`, `allowed_stages`); this panel never decides anything itself.
const OPEN_FOR_SETUP = (lead) => !!lead.assigned_to && !['new', 'won', 'lost'].includes(lead.pipeline_stage);

const ConversionPanel = ({ lead, language, onConverted }) => {
  const { can } = useSalesAccess();
  const [status, setStatus] = useState(null);           // null = loading, false = could not be loaded
  const [setupOpen, setSetupOpen] = useState(false);
  const [notInterestedOpen, setNotInterestedOpen] = useState(false);
  const won = lead.pipeline_stage === 'won';
  const show = can('sales.customers.view') && (won || (OPEN_FOR_SETUP(lead) && can('sales.customers.setup')));

  const load = useCallback(async () => {
    try {
      const res = await api.get(`/sales/leads/${lead.id}/conversion`);
      setStatus(res.data);
    } catch (err) {
      setStatus(false);
    }
  }, [lead.id]);

  useEffect(() => { if (show) load(); }, [show, load, lead.archived_at, lead.pipeline_stage]);

  if (!show || status === null || status === false) return null;

  const converted = status.converted ? status.customer : null;
  const done = (result) => { load(); onConverted && onConverted(result); };
  const canNotInterested = !won && lead.can.change_stage && lead.allowed_stages.includes('lost');

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
          ) : status.can_setup ? (
            <p className="text-sm text-gray-700 mt-2" data-testid="conversion-ready">{ts(won ? 'setup_panel_ready_won' : 'setup_panel_ready_open', language)}</p>
          ) : status.setup_blocker === 'lead_archived' ? (
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
          {!converted && status.can_setup && canNotInterested && (
            <Button variant="outline" className="rounded-sm text-red-700 border-red-200 hover:bg-red-50" onClick={() => setNotInterestedOpen(true)} data-testid="lead-not-interested-btn">
              <ThumbsDown className="w-4 h-4 me-2" aria-hidden="true" />{ts('wf_not_interested', language)}
            </Button>
          )}
          {!converted && status.can_setup && (
            <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => setSetupOpen(true)} data-testid="setup-btn">
              <Handshake className="w-4 h-4 me-2" aria-hidden="true" />{ts(won ? 'setup_action' : 'wf_agreed', language)}
            </Button>
          )}
        </div>
      </div>
      <CustomerSetupWizard open={setupOpen} onOpenChange={setSetupOpen} lead={lead} language={language} onDone={done} />
      <NotInterestedDialog open={notInterestedOpen} onOpenChange={setNotInterestedOpen} lead={lead} language={language} onDone={done} />
    </Card>
  );
};

export default ConversionPanel;
