import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { digitsOnly, formatDate, sourceName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import { PriorityBadge, StageBadge } from '@/components/sales/LeadBadges';
import NotInterestedDialog from '@/components/sales/NotInterestedDialog';
import CustomerSetupWizard from '@/components/sales/CustomerSetupWizard';
import { ChevronLeft, ChevronRight, Handshake, Hourglass, Inbox, Mail, MapPin, MessageCircle, Phone, ThumbsDown } from 'lucide-react';
import { apiErrorMessage } from '@/utils/salesErrors';
import { toast } from 'sonner';

// The Sales Employee's primary workflow (simplified workflow): ONE of their open assigned leads at a time - the longest-waiting
// first, from GET /sales/queue - with everything needed to call it and the two outcomes:
//   * Not interested -> the lead is marked lost with a reason (kept, with its timeline) and the next lead shows;
//   * Agreed / Interested -> the Customer Setup wizard (Lead -> Customer -> JAZ Company -> Subscription).
// The server decides everything (whose leads, the order, what each action may do); this card only shows it.
const Line = ({ icon: Icon, children, testid }) => (
  <p className="flex items-start gap-2 text-sm text-[#0A0A0A]" data-testid={testid}>
    <Icon className="w-4 h-4 mt-0.5 text-gray-400 shrink-0" aria-hidden="true" />
    <span className="min-w-0 break-words">{children}</span>
  </p>
);

const MyLeadWorkspace = ({ language }) => {
  const { can } = useSalesAccess();
  const ref = useSalesReference({});
  const [position, setPosition] = useState(0);
  const [data, setData] = useState(null);               // null = loading
  const [error, setError] = useState(false);
  const [notInterestedOpen, setNotInterestedOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  // The lead a decision dialog was opened for: the queue behind it reloads (a wait-listed lead moves to the end), and a dialog must
  // never end up deciding on a different lead than the one the employee was looking at when they opened it.
  const [deciding, setDeciding] = useState(null);

  const load = useCallback(async (at) => {
    setError(false);
    try {
      const res = await api.get('/sales/queue', { params: { position: at } });
      setData(res.data);
      setPosition(res.data.position);
    } catch (err) {
      setError(true);
    }
  }, []);

  useEffect(() => { load(0); }, [load]);

  const lead = data?.lead;
  const canDecide = lead && can('sales.leads.change_stage') && !lead.archived_at;
  const canSetup = canDecide && can('sales.customers.setup');
  const decided = () => load(position);                     // the decided lead left the queue: the same position is the next one

  // Wait List: the lead stays theirs and goes to the end of their queue (a timestamp and a pending status - nothing else).
  const [waiting, setWaiting] = useState(false);
  const waitList = async () => {
    setWaiting(true);
    try {
      await api.post(`/sales/leads/${lead.id}/wait-list`);
      toast.success(ts('bt_toast_wait_listed', language));
      await load(position);
    } catch (err) {
      toast.error(apiErrorMessage(err, language));
    }
    setWaiting(false);
  };

  return (
    <Card className="p-5 md:p-6 bg-white border border-gray-200 rounded-md" data-testid="my-lead-workspace" aria-busy={data === null}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-[#0A0A0A]">{ts('wf_queue_title', language)}</h2>
        {data && data.total > 0 && (
          <div className="flex items-center gap-2" data-testid="queue-nav">
            <Button variant="outline" size="icon" className="rounded-sm h-9 w-9" disabled={position <= 0 || waiting} onClick={() => load(position - 1)}
              aria-label={ts('wf_queue_prev', language)} data-testid="queue-prev">
              <ChevronLeft className="w-4 h-4 rtl:rotate-180" aria-hidden="true" />
            </Button>
            <span className="text-sm text-gray-600" data-testid="queue-counter">
              {ts('wf_queue_counter', language).replace('{n}', position + 1).replace('{total}', data.total)}
            </span>
            <Button variant="outline" size="icon" className="rounded-sm h-9 w-9" disabled={position >= data.total - 1 || waiting} onClick={() => load(position + 1)}
              aria-label={ts('wf_queue_next', language)} data-testid="queue-next">
              <ChevronRight className="w-4 h-4 rtl:rotate-180" aria-hidden="true" />
            </Button>
          </div>
        )}
      </div>

      {error && (
        <div className="py-8 text-center" data-testid="queue-error">
          <p className="text-gray-600 mb-3">{ts('wf_queue_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={() => load(position)}>{ts('action_retry', language)}</Button>
        </div>
      )}

      {!error && data === null && <p className="py-8 text-center text-gray-500">{ts('loading', language)}</p>}

      {!error && data && !lead && (
        <div className="py-10 text-center" data-testid="queue-empty">
          <Inbox className="w-12 h-12 mx-auto text-gray-300 mb-3" aria-hidden="true" />
          <p className="font-medium text-gray-700">{ts('wf_queue_empty_title', language)}</p>
          <p className="text-sm text-gray-500 mt-1">{ts('wf_queue_empty_body', language)}</p>
        </div>
      )}

      {!error && lead && (
        <div className="mt-4 space-y-5" data-testid="queue-lead">
          <div>
            <p className="text-2xl md:text-3xl font-bold text-[#0A0A0A] break-words" data-testid="queue-lead-name"><bdi>{lead.business_name}</bdi></p>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <StageBadge stage={lead.pipeline_stage} language={language} />
              <PriorityBadge priority={lead.priority} language={language} />
              {lead.business_type && <span className="text-sm text-gray-500" dir="auto">{lead.business_type}</span>}
              {lead.assigned_at && <span className="text-xs text-gray-500">{ts('wf_assigned_on', language).replace('{date}', formatDate(lead.assigned_at, language))}</span>}
              {lead.wait_listed_at && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-xs text-amber-800" data-testid="queue-wait-badge">
                  <Hourglass className="w-3 h-3" aria-hidden="true" />{ts('bt_wait_listed_since', language).replace('{date}', formatDate(lead.wait_listed_at, language))}
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
            {lead.contact_name && (
              <p className="text-sm text-[#0A0A0A] md:col-span-2" data-testid="queue-contact">
                <bdi className="font-medium">{lead.contact_name}</bdi>
                {lead.contact_position && <span className="text-gray-500"> · <bdi>{lead.contact_position}</bdi></span>}
              </p>
            )}
            {lead.phone && (
              <Line icon={Phone} testid="queue-phone">
                <a href={`tel:${lead.phone.replace(/[^\d+]/g, '')}`} className="text-[#0033A0] hover:underline font-medium"><bdi dir="ltr">{lead.phone}</bdi></a>
              </Line>
            )}
            {lead.whatsapp && (
              <Line icon={MessageCircle} testid="queue-whatsapp">
                <a href={`https://wa.me/${digitsOnly(lead.whatsapp)}`} target="_blank" rel="noopener noreferrer" className="text-[#0033A0] hover:underline"><bdi dir="ltr">{lead.whatsapp}</bdi></a>
              </Line>
            )}
            {lead.email && (
              <Line icon={Mail} testid="queue-email">
                <a href={`mailto:${lead.email}`} className="text-[#0033A0] hover:underline"><bdi dir="ltr">{lead.email}</bdi></a>
              </Line>
            )}
            {(lead.city || lead.country || lead.address) && (
              <Line icon={MapPin} testid="queue-location">
                <bdi>{[lead.city, lead.country].filter(Boolean).join(', ')}</bdi>
                {lead.address && <span className="block text-gray-500" dir="auto">{lead.address}</span>}
              </Line>
            )}
            <p className="text-sm text-gray-600 md:col-span-2" data-testid="queue-source">
              {ts('lead_field_source', language)}: {sourceName(ref.sources, lead.source, language)}
              {lead.campaign && <> · {ts('lead_field_campaign', language)}: <bdi>{lead.campaign.name}</bdi></>}
            </p>
          </div>

          {(lead.description || lead.notes) && (
            <div className="rounded-md bg-gray-50 border border-gray-200 px-4 py-3 space-y-2">
              {lead.description && <p className="text-sm text-gray-700 whitespace-pre-wrap break-words" dir="auto">{lead.description}</p>}
              {lead.notes && <p className="text-sm text-gray-700 whitespace-pre-wrap break-words" dir="auto">{lead.notes}</p>}
            </div>
          )}

          {canDecide && (
            <div className="border-t border-gray-100 pt-4">
              <p className="text-sm text-gray-600 mb-3">{ts('wf_decide_hint', language)}</p>
              <div className={`grid grid-cols-1 gap-3 ${lead.can?.wait_list ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
                <Button variant="outline" className="h-12 rounded-sm text-base text-red-700 border-red-200 hover:bg-red-50"
                  onClick={() => { setDeciding(lead); setNotInterestedOpen(true); }} disabled={waiting || !lead.allowed_stages.includes('lost')} data-testid="queue-not-interested">
                  <ThumbsDown className="w-5 h-5 me-2" aria-hidden="true" />{ts('wf_not_interested', language)}
                </Button>
                {lead.can?.wait_list && (
                  <Button variant="outline" className="h-12 rounded-sm text-base text-amber-800 border-amber-200 hover:bg-amber-50" onClick={waitList} disabled={waiting}
                    title={ts('bt_wait_hint', language)} data-testid="queue-wait-list">
                    <Hourglass className="w-5 h-5 me-2" aria-hidden="true" />{ts('bt_wait_list', language)}
                  </Button>
                )}
                {canSetup && (
                  <Button className="h-12 rounded-sm text-base bg-[#0033A0] hover:bg-[#002277]" onClick={() => { setDeciding(lead); setSetupOpen(true); }} disabled={waiting} data-testid="queue-agreed">
                    <Handshake className="w-5 h-5 me-2" aria-hidden="true" />{ts('wf_agreed', language)}
                  </Button>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-4 text-sm">
            <Link to={`/sales/leads/${lead.id}`} className="text-[#0033A0] hover:underline" data-testid="queue-open-lead">{ts('wf_open_lead', language)}</Link>
            <Link to="/sales/leads?assigned_to=me" className="text-[#0033A0] hover:underline">{ts('wf_all_my_leads', language)}</Link>
          </div>

          <NotInterestedDialog open={notInterestedOpen} onOpenChange={setNotInterestedOpen} lead={deciding || lead} language={language} onDone={decided} />
          <CustomerSetupWizard open={setupOpen} onOpenChange={setSetupOpen} lead={deciding || lead} language={language} onDone={decided} />
        </div>
      )}
    </Card>
  );
};

export default MyLeadWorkspace;
