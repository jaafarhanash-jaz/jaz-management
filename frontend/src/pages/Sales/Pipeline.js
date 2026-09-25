import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { STAGES, formatValue, stageName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import StageChangeDialog from '@/components/sales/StageChangeDialog';
import { PriorityBadge } from '@/components/sales/LeadBadges';
import { ArrowRightLeft, Plus } from 'lucide-react';

const PER_COLUMN = 20;
const ALL = '__all__';

// The pipeline as a board: one column per stage, the most recently touched leads first. Moving a lead goes through the
// same dialog (and the same server-side rules) as on the lead page - the board never decides what is allowed, it only
// offers the moves the server listed for each lead in `allowed_stages`.
const SalesPipeline = ({ onLogout, language, setLanguage, userRole }) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  const ref = useSalesReference({ campaigns: can('sales.campaigns.view'), assignees: can('sales.leads.assign') });

  const [campaignId, setCampaignId] = useState('');
  const [assignedTo, setAssignedTo] = useState('');
  const [columns, setColumns] = useState(null);       // { stage: {items, total} }
  const [error, setError] = useState(false);
  const [moving, setMoving] = useState(null);         // the lead whose stage dialog is open
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const id = ++requestRef.current;
    setError(false);
    const base = { limit: PER_COLUMN, sort: 'updated_at', order: 'desc' };
    if (campaignId) base.campaign_id = campaignId;
    if (assignedTo) base.assigned_to = assignedTo;
    try {
      const results = await Promise.all(STAGES.map((stage) => api.get('/sales/leads', { params: { ...base, pipeline_stage: stage } })));
      if (id !== requestRef.current) return;
      const next = {};
      STAGES.forEach((stage, i) => { next[stage] = { items: results[i].data.items, total: results[i].data.total }; });
      setColumns(next);
    } catch (e) {
      if (id === requestRef.current) setError(true);
    }
  }, [campaignId, assignedTo]);

  useEffect(() => { if (!accessLoading && hasModule('pipeline')) load(); }, [accessLoading, hasModule, load]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('pipeline')) return shell(<SalesNoAccess language={language} />);

  const filterQuery = (stage) => {
    const q = new URLSearchParams({ pipeline_stage: stage });
    if (campaignId) q.set('campaign_id', campaignId);
    if (assignedTo) q.set('assigned_to', assignedTo);
    return q.toString();
  };

  const assigneeOptions = [
    { value: 'me', label: ts('filter_assigned_me', language) },
    { value: 'unassigned', label: ts('filter_unassigned', language) },
    ...ref.assignees.map((a) => ({ value: a.id, label: a.name })),
  ];

  return shell(
    <div className="space-y-5">
      <div className="flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-pipeline-title">{ts('pipeline_title', language)}</h1>
          <p className="text-sm text-gray-500 mt-1">{ts('pipeline_subtitle', language)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" className="rounded-sm"><Link to="/sales/leads">{ts('nav_leads', language)}</Link></Button>
          {can('sales.leads.create') && (
            <Button asChild className="bg-[#0033A0] hover:bg-[#002277] rounded-sm"><Link to="/sales/leads/new"><Plus className="w-4 h-4 me-2" aria-hidden="true" />{ts('leads_add', language)}</Link></Button>
          )}
        </div>
      </div>

      <Card className="p-4 bg-white border border-gray-200 rounded-md">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl">
          {can('sales.campaigns.view') && (
            <div>
              <Label htmlFor="pl-campaign" className="text-xs text-gray-600">{ts('lead_field_campaign', language)}</Label>
              <Select value={campaignId || ALL} onValueChange={(v) => setCampaignId(v === ALL ? '' : v)}>
                <SelectTrigger id="pl-campaign" className="mt-1 h-10" data-testid="pipeline-campaign"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{ts('filter_all', language)}</SelectItem>
                  {ref.campaigns.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
          <div>
            <Label htmlFor="pl-assigned" className="text-xs text-gray-600">{ts('filter_assigned', language)}</Label>
            <Select value={assignedTo || ALL} onValueChange={(v) => setAssignedTo(v === ALL ? '' : v)}>
              <SelectTrigger id="pl-assigned" className="mt-1 h-10" data-testid="pipeline-assigned"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{ts('filter_all', language)}</SelectItem>
                {assigneeOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
      </Card>

      {error ? (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid="pipeline-error">
          <p className="text-gray-600 mb-4">{ts('leads_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button>
        </Card>
      ) : !columns ? (
        <div className="text-center py-12 text-gray-500" data-testid="pipeline-loading">{ts('loading', language)}</div>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4" data-testid="pipeline-board">
          {STAGES.map((stage) => {
            const column = columns[stage];
            return (
              <section key={stage} className="w-72 shrink-0" aria-labelledby={`col-${stage}`} data-testid={`column-${stage}`}>
                <div className="flex items-center justify-between rounded-t-md border border-b-0 border-gray-200 bg-gray-50 px-3 py-2.5">
                  <h2 id={`col-${stage}`} className="text-sm font-semibold text-[#0A0A0A]">{stageName(stage, language)}</h2>
                  <span className="text-xs rounded-full bg-white border border-gray-200 px-2 py-0.5 text-gray-600" data-testid={`column-count-${stage}`}>{column.total}</span>
                </div>
                <div className="rounded-b-md border border-gray-200 bg-gray-50/50 p-2 space-y-2 max-h-[65vh] overflow-y-auto">
                  {column.items.length === 0 ? (
                    <p className="text-xs text-gray-400 text-center py-6">{ts('pipeline_empty_column', language)}</p>
                  ) : column.items.map((lead) => (
                    <Card key={lead.id} className="p-3 bg-white border border-gray-200 rounded-md" data-testid={`card-${lead.id}`}>
                      <Link to={`/sales/leads/${lead.id}`} className="block font-medium text-sm text-[#0033A0] hover:underline break-words">{lead.business_name}</Link>
                      <p className="text-xs text-gray-500 mt-0.5">{[lead.city, lead.contact_name].filter(Boolean).join(' · ') || ' '}</p>
                      <div className="flex flex-wrap items-center gap-1.5 mt-2">
                        <PriorityBadge priority={lead.priority} language={language} />
                        {lead.estimated_value !== null && <span className="text-xs text-gray-600" dir="ltr">{formatValue(lead.estimated_value, language)}</span>}
                      </div>
                      <p className="text-xs text-gray-600 mt-2">{lead.assigned_to ? lead.assigned_to.name : ts('unassigned', language)}</p>
                      {lead.can.change_stage && lead.allowed_stages.length > 0 && (
                        <Button variant="outline" size="sm" className="rounded-sm mt-2 w-full min-h-[36px]" onClick={() => setMoving(lead)} data-testid={`move-${lead.id}`}
                          aria-label={`${ts('action_change_stage', language)}: ${lead.business_name}`}>
                          <ArrowRightLeft className="w-3.5 h-3.5 me-1.5" aria-hidden="true" />{ts('action_change_stage', language)}
                        </Button>
                      )}
                    </Card>
                  ))}
                  {column.total > column.items.length && (
                    <Link to={`/sales/leads?${filterQuery(stage)}`} className="block text-center text-sm text-[#0033A0] hover:underline py-1" data-testid={`view-all-${stage}`}>
                      {ts('pipeline_view_all', language).replace('{n}', column.total)}
                    </Link>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      <StageChangeDialog open={!!moving} onOpenChange={(o) => { if (!o) setMoving(null); }} lead={moving} language={language} onDone={() => { setMoving(null); load(); }} />
    </div>,
  );
};

export default SalesPipeline;
