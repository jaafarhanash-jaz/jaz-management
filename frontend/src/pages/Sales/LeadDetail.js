import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { STAGES, digitsOnly, formatDate, formatDateTime, formatValue, lostReasonName, sourceName, stageName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import AssignDialog from '@/components/sales/AssignDialog';
import StageChangeDialog from '@/components/sales/StageChangeDialog';
import LeadTimeline from '@/components/sales/LeadTimeline';
import LeadActivities from '@/components/sales/LeadActivities';
import ConversionPanel from '@/components/sales/ConversionPanel';
import { suggestedStage } from '@/utils/salesActivities';
import { PriorityBadge, StageBadge } from '@/components/sales/LeadBadges';
import { Archive, ArchiveRestore, ArrowLeft, ArrowRightLeft, Building2, Globe, Mail, MapPin, MessageCircle, Pencil, Phone, UserMinus, UserPlus } from 'lucide-react';
import { toast } from 'sonner';

// The stages shown on the progress bar. `lost` is not a step: a lost lead shows its reason instead.
const FLOW = STAGES.filter((s) => s !== 'lost');

// A URL typed without a scheme is treated as https; anything that is not http(s) can never become a link's target
// (so a stored "javascript:..." string is just text).
const webHref = (url) => (/^https?:\/\//i.test(url) ? url : `https://${url}`);

const Row = ({ icon: Icon, label, children, testid }) => (
  <div className="flex items-start gap-3 py-2" data-testid={testid}>
    {Icon && <Icon className="w-4 h-4 mt-0.5 text-gray-400 shrink-0" aria-hidden="true" />}
    <div className="min-w-0">
      <p className="text-xs text-gray-500">{label}</p>
      <div className="text-sm text-[#0A0A0A] break-words">{children}</div>
    </div>
  </div>
);

const Empty = () => <span className="text-gray-400">-</span>;
const Ltr = ({ children }) => <bdi dir="ltr">{children}</bdi>;

const SalesLeadDetail = ({ onLogout, language, setLanguage, userRole }) => {
  const { leadId } = useParams();
  const { hasModule, loading: accessLoading } = useSalesAccess();
  const ref = useSalesReference({});

  const [lead, setLead] = useState(null);
  const [state, setState] = useState('loading');     // loading | ready | forbidden | notfound | error
  const [timelineKey, setTimelineKey] = useState(0);
  const [stageOpen, setStageOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState(null);      // { stage } - the stage OFFERED after some activity (never applied by itself)
  const [stageInitial, setStageInitial] = useState(null);  // pre-selects that stage when the person accepts the offer

  const load = useCallback(async () => {
    setState('loading');
    try {
      const res = await api.get(`/sales/leads/${leadId}`);
      setLead(res.data);
      setState('ready');
    } catch (err) {
      const status = err.response?.status;
      setState(status === 403 ? 'forbidden' : status === 404 ? 'notfound' : 'error');
    }
  }, [leadId]);

  useEffect(() => { if (!accessLoading && hasModule('leads')) load(); }, [accessLoading, hasModule, load]);

  const changed = (updated, messageKey) => {
    setLead(updated);
    setTimelineKey((k) => k + 1);
    if (messageKey) toast.success(ts(messageKey, language));
  };

  // Something was logged / changed on the lead's calls, follow-ups, demos or trials: refresh the timeline and the lead (its
  // `allowed_stages` come from the server), and OFFER - never apply - the pipeline stage that usually follows.
  const SUGGESTING = { call_created: 'call', demo_scheduled: 'demo_scheduled', demo_completed: 'demo_completed', trial_started: 'trial_started' };
  const workChanged = async (eventType, item) => {
    setTimelineKey((k) => k + 1);
    setSuggestion(null);
    try {
      const res = await api.get(`/sales/leads/${leadId}`);
      setLead(res.data);
      const stage = SUGGESTING[eventType] ? suggestedStage(SUGGESTING[eventType], item, res.data) : null;
      if (stage) setSuggestion({ stage });
    } catch (err) {
      // the change itself succeeded; if the refresh fails the page keeps showing what it has
    }
  };

  // The customer setup / "not interested" changed the lead (won / lost): re-read it quietly and refresh the timeline.
  const refreshLead = async () => {
    setTimelineKey((k) => k + 1);
    try {
      const res = await api.get(`/sales/leads/${leadId}`);
      setLead(res.data);
    } catch (err) {
      // the change itself succeeded; if the refresh fails the page keeps showing what it has
    }
  };

  const act = async (request, messageKey) => {
    setBusy(true);
    try {
      const res = await request();
      changed(res.data.lead, messageKey);
    } catch (err) {
      toast.error(apiErrorMessage(err, language));
    }
    setBusy(false);
  };

  const archive = () => { if (window.confirm(ts('confirm_archive', language))) act(() => api.delete(`/sales/leads/${lead.id}`), 'toast_lead_archived'); };
  const restore = () => act(() => api.post(`/sales/leads/${lead.id}/restore`), 'toast_lead_restored');
  const unassign = () => { if (window.confirm(ts('confirm_unassign', language))) act(() => api.post(`/sales/leads/${lead.id}/unassign`), 'toast_unassigned'); };

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  const back = <Button asChild variant="outline" className="rounded-sm mt-4"><Link to="/sales/leads">{ts('action_back_to_leads', language)}</Link></Button>;

  if (accessLoading || state === 'loading') return shell(<div className="text-center py-12 text-gray-500" data-testid="lead-loading">{ts('loading', language)}</div>);
  if (!hasModule('leads') || state === 'forbidden') {
    return shell(<><SalesNoAccess language={language} /><div className="text-center">{back}</div></>);
  }
  if (state === 'notfound') {
    return shell(<Card className="p-12 text-center bg-white border border-gray-200" data-testid="lead-not-found"><p className="text-gray-700 font-medium">{ts('lead_not_found', language)}</p>{back}</Card>);
  }
  if (state === 'error') {
    return shell(
      <Card className="p-12 text-center bg-white border border-gray-200" data-testid="lead-load-error">
        <p className="text-gray-700 font-medium">{ts('lead_load_error', language)}</p>
        <div className="flex justify-center gap-2 mt-4"><Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button></div>
      </Card>,
    );
  }

  const can = lead.can;
  const archived = !!lead.archived_at;
  const stageIndex = FLOW.indexOf(lead.pipeline_stage);
  const owner = lead.assigned_to;

  return shell(
    <div className="space-y-5" data-testid="lead-detail">
      <div>
        <Button asChild variant="ghost" size="sm" className="rounded-sm -ms-2 mb-2 text-gray-600">
          <Link to="/sales/leads"><ArrowLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_back_to_leads', language)}</Link>
        </Button>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-3xl md:text-4xl font-bold text-[#0A0A0A] break-words" data-testid="lead-title">{lead.business_name}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <StageBadge stage={lead.pipeline_stage} language={language} />
              <PriorityBadge priority={lead.priority} language={language} />
              {archived && <span className="text-xs rounded-full border border-gray-300 px-2 py-0.5 text-gray-600" data-testid="lead-archived-tag">{ts('lead_archived_tag', language)}</span>}
              {lead.business_type && <span className="text-sm text-gray-500">{lead.business_type}</span>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2" data-testid="lead-actions">
            {can.update && (
              <Button asChild variant="outline" className="rounded-sm"><Link to={`/sales/leads/${lead.id}/edit`} data-testid="lead-edit-btn"><Pencil className="w-4 h-4 me-2" aria-hidden="true" />{ts('action_edit', language)}</Link></Button>
            )}
            {can.change_stage && lead.allowed_stages.length > 0 && (
              <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => setStageOpen(true)} disabled={busy} data-testid="lead-stage-btn">
                <ArrowRightLeft className="w-4 h-4 me-2" aria-hidden="true" />{ts('action_change_stage', language)}
              </Button>
            )}
            {can.assign && (
              <Button variant="outline" className="rounded-sm" onClick={() => setAssignOpen(true)} disabled={busy} data-testid="lead-assign-btn">
                <UserPlus className="w-4 h-4 me-2" aria-hidden="true" />{ts(owner ? 'action_reassign' : 'action_assign', language)}
              </Button>
            )}
            {can.assign && owner && (
              <Button variant="outline" className="rounded-sm" onClick={unassign} disabled={busy} data-testid="lead-unassign-btn">
                <UserMinus className="w-4 h-4 me-2" aria-hidden="true" />{ts('action_unassign', language)}
              </Button>
            )}
            {can.archive && (
              <Button variant="outline" className="rounded-sm text-red-700 border-red-200 hover:bg-red-50" onClick={archive} disabled={busy} data-testid="lead-archive-btn">
                <Archive className="w-4 h-4 me-2" aria-hidden="true" />{ts('action_archive', language)}
              </Button>
            )}
            {can.restore && (
              <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={restore} disabled={busy} data-testid="lead-restore-btn">
                <ArchiveRestore className="w-4 h-4 me-2" aria-hidden="true" />{ts('action_restore', language)}
              </Button>
            )}
          </div>
        </div>
      </div>

      {archived && (
        <div className="rounded-md border border-gray-300 bg-gray-50 px-4 py-3 text-sm text-gray-700" role="status" data-testid="lead-archived-banner">
          {ts('lead_archived_banner', language)}
        </div>
      )}

      {can.edit_locked && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status" data-testid="lead-edit-locked">
          {ts('lead_edit_locked_note', language)}
        </div>
      )}

      {suggestion && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 flex flex-wrap items-center gap-3" role="status" data-testid="stage-suggestion">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[#0A0A0A]">{ts('suggest_title', language).replace('{stage}', stageName(suggestion.stage, language))}</p>
            <p className="text-xs text-gray-600 mt-0.5">{ts('suggest_hint', language)}</p>
          </div>
          <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => { setStageInitial(suggestion.stage); setSuggestion(null); setStageOpen(true); }} data-testid="stage-suggestion-move">
            {ts('suggest_move', language).replace('{stage}', stageName(suggestion.stage, language))}
          </Button>
          <Button variant="ghost" className="rounded-sm" onClick={() => setSuggestion(null)} data-testid="stage-suggestion-dismiss">{ts('suggest_dismiss', language)}</Button>
        </div>
      )}

      {/* pipeline progress */}
      <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-pipeline">
        <h2 className="text-lg font-semibold text-[#0A0A0A] mb-4">{ts('lead_section_pipeline', language)}</h2>
        <div className="overflow-x-auto">
          <ol className="flex items-center gap-2 min-w-max pb-1" aria-label={ts('lead_section_pipeline', language)}>
            {FLOW.map((stage, i) => {
              const isCurrent = stage === lead.pipeline_stage;
              const done = stageIndex >= 0 && i < stageIndex;
              return (
                <li key={stage} className="flex items-center gap-2" aria-current={isCurrent ? 'step' : undefined}>
                  <span className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${
                    isCurrent ? (stage === 'won' ? 'bg-green-600 text-white border-green-600' : 'bg-[#0033A0] text-white border-[#0033A0]')
                      : done ? 'bg-blue-50 text-[#0033A0] border-blue-200' : 'bg-white text-gray-500 border-gray-200'}`}>
                    {stageName(stage, language)}
                  </span>
                  {i < FLOW.length - 1 && <span className="w-4 h-px bg-gray-300" aria-hidden="true" />}
                </li>
              );
            })}
          </ol>
        </div>
        {lead.pipeline_stage === 'lost' && (
          <p className="mt-3 text-sm rounded-md border border-red-200 bg-red-50 text-red-800 px-3 py-2" data-testid="lead-lost-reason">
            {ts('lead_field_lost_reason', language)}: <strong>{lostReasonName(lead.lost_reason, language)}</strong>
            {lead.closed_at && <span className="text-red-700"> · {formatDate(lead.closed_at, language)}</span>}
          </p>
        )}
        {lead.pipeline_stage === 'won' && lead.closed_at && (
          <p className="mt-3 text-sm text-green-700">{ts('lead_won_on', language).replace('{date}', formatDate(lead.closed_at, language))}</p>
        )}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-contact">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('lead_section_contact', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row label={ts('lead_field_contact_name', language)} testid="row-contact_name">
              {lead.contact_name ? <>{lead.contact_name}{lead.contact_position && <span className="text-gray-500"> · {lead.contact_position}</span>}</> : <Empty />}
            </Row>
            <Row icon={Phone} label={ts('lead_field_phone', language)} testid="row-phone">
              {lead.phone ? <a href={`tel:${lead.phone.replace(/[^\d+]/g, '')}`} className="text-[#0033A0] hover:underline"><Ltr>{lead.phone}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={MessageCircle} label={ts('lead_field_whatsapp', language)} testid="row-whatsapp">
              {lead.whatsapp ? <a href={`https://wa.me/${digitsOnly(lead.whatsapp)}`} target="_blank" rel="noopener noreferrer" className="text-[#0033A0] hover:underline"><Ltr>{lead.whatsapp}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={Mail} label={ts('lead_field_email', language)} testid="row-email">
              {lead.email ? <a href={`mailto:${lead.email}`} className="text-[#0033A0] hover:underline"><Ltr>{lead.email}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={Globe} label={ts('lead_field_website', language)} testid="row-website">
              {lead.website ? <a href={webHref(lead.website)} target="_blank" rel="noopener noreferrer nofollow" className="text-[#0033A0] hover:underline"><Ltr>{lead.website}</Ltr></a> : <Empty />}
            </Row>
          </div>
        </Card>

        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-location">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('lead_section_location', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row icon={MapPin} label={ts('lead_field_city', language)} testid="row-city">
              {lead.city || lead.country ? [lead.city, lead.country].filter(Boolean).join(', ') : <Empty />}
            </Row>
            <Row label={ts('lead_field_address', language)} testid="row-address">{lead.address || <Empty />}</Row>
            <Row label={ts('lead_field_coordinates', language)} testid="row-coordinates">
              {lead.latitude !== null && lead.longitude !== null ? (
                <a href={`https://www.openstreetmap.org/?mlat=${lead.latitude}&mlon=${lead.longitude}#map=16/${lead.latitude}/${lead.longitude}`}
                  target="_blank" rel="noopener noreferrer" className="text-[#0033A0] hover:underline"><Ltr>{lead.latitude}, {lead.longitude}</Ltr></a>
              ) : <Empty />}
            </Row>
          </div>
        </Card>

        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-sales">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('lead_section_sales', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row label={ts('lead_field_source', language)} testid="row-source">{sourceName(ref.sources, lead.source, language)}</Row>
            <Row icon={Building2} label={ts('lead_field_campaign', language)} testid="row-campaign">
              {lead.campaign ? <Link to={`/sales/leads?campaign_id=${lead.campaign.id}`} className="text-[#0033A0] hover:underline">{lead.campaign.name}</Link> : <Empty />}
            </Row>
            <Row label={ts('lead_field_estimated_value', language)} testid="row-value">{lead.estimated_value !== null ? <Ltr>{formatValue(lead.estimated_value, language)}</Ltr> : <Empty />}</Row>
            <Row label={ts('lead_field_assigned_to', language)} testid="row-assigned">
              {owner ? (
                <>{owner.name}{owner.status === 'inactive' && <span className="ms-2 text-xs text-red-600">({ts('assignee_inactive', language)})</span>}
                  {lead.assigned_at && <span className="block text-xs text-gray-500">{ts('lead_assigned_on', language).replace('{date}', formatDate(lead.assigned_at, language))}</span>}</>
              ) : <span className="text-gray-500">{ts('unassigned', language)}</span>}
            </Row>
            <Row label={ts('lead_created_by', language)} testid="row-created">
              {lead.created_by?.name || <Empty />}<span className="block text-xs text-gray-500">{formatDateTime(lead.created_at, language)}</span>
            </Row>
            <Row label={ts('lead_updated_at', language)} testid="row-updated">{formatDateTime(lead.updated_at, language)}</Row>
          </div>
        </Card>
      </div>

      {(lead.description || lead.notes) && (
        <Card className="p-5 bg-white border border-gray-200 rounded-md space-y-4" data-testid="lead-notes">
          {lead.description && (
            <div>
              <h2 className="text-lg font-semibold text-[#0A0A0A] mb-1">{ts('lead_field_description', language)}</h2>
              <p className="text-sm text-gray-700 whitespace-pre-wrap break-words" dir="auto">{lead.description}</p>
            </div>
          )}
          {lead.notes && (
            <div>
              <h2 className="text-lg font-semibold text-[#0A0A0A] mb-1">{ts('lead_field_notes', language)}</h2>
              <p className="text-sm text-gray-700 whitespace-pre-wrap break-words" dir="auto">{lead.notes}</p>
            </div>
          )}
        </Card>
      )}

      <ConversionPanel lead={lead} language={language} onConverted={refreshLead} />

      <LeadActivities lead={lead} language={language} onChanged={workChanged} />

      <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="lead-timeline-card">
        <h2 className="text-lg font-semibold text-[#0A0A0A]">{ts('lead_section_timeline', language)}</h2>
        <p className="text-xs text-gray-500 mt-0.5 mb-4">{ts('lead_section_timeline_hint', language)}</p>
        <LeadTimeline leadId={lead.id} language={language} sources={ref.sources} refreshKey={timelineKey} />
      </Card>

      <StageChangeDialog open={stageOpen} onOpenChange={(open) => { setStageOpen(open); if (!open) setStageInitial(null); }} lead={lead} language={language} initialStage={stageInitial} onDone={(updated) => changed(updated)} />
      <AssignDialog open={assignOpen} onOpenChange={setAssignOpen} leadIds={[lead.id]} currentAssigneeId={owner?.id || null} language={language} onDone={(updated) => changed(updated)} />
    </div>,
  );
};

export default SalesLeadDetail;
