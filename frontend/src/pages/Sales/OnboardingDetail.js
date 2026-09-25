import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { ONBOARDING_STAGES, onboardingStageName } from '@/utils/salesCustomers';
import { digitsOnly, formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { CustomerStatusBadge, OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { Empty, Ltr, Row } from '@/components/sales/DetailRow';
import { Person } from '@/components/sales/ActivityItems';
import { useOnboardingActions } from '@/components/sales/OnboardingDialogs';
import OnboardingTimeline from '@/components/sales/OnboardingTimeline';
import { ArrowLeft, ClipboardList, Eye, Mail, MapPin, MessageCircle, NotebookPen, Phone, UserPlus } from 'lucide-react';

// One onboarding record: where the customer is in the workflow, who owns it, the customer's contact details (what an Onboarding
// Employee needs to do the work - and nothing behind them: no lead, no company, unless the caller may also view the customer),
// the working notes, and the record's own immutable history. Every button follows what the server says the caller may do.
const SalesOnboardingDetail = ({ onLogout, language, setLanguage, userRole }) => {
  const { onboardingId } = useParams();
  const { hasModule, loading: accessLoading } = useSalesAccess();
  const [record, setRecord] = useState(null);
  const [state, setState] = useState('loading');     // loading | ready | forbidden | notfound | error
  const [timelineKey, setTimelineKey] = useState(0);

  const load = useCallback(async () => {
    setState('loading');
    try {
      const res = await api.get(`/sales/onboarding/${onboardingId}`);
      setRecord(res.data);
      setState('ready');
    } catch (err) {
      const status = err.response?.status;
      setState(status === 403 ? 'forbidden' : status === 404 ? 'notfound' : 'error');
    }
  }, [onboardingId]);

  useEffect(() => { if (!accessLoading && hasModule('onboarding')) load(); }, [accessLoading, hasModule, load]);

  const actions = useOnboardingActions({ language, onChanged: (event, updated) => { setRecord(updated); setTimelineKey((k) => k + 1); } });

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  const back = <Button asChild variant="outline" className="rounded-sm mt-4"><Link to="/sales/onboarding">{ts('onb_back', language)}</Link></Button>;

  if (accessLoading || state === 'loading') return shell(<div className="text-center py-12 text-gray-500" data-testid="onb-loading">{ts('loading', language)}</div>);
  if (!hasModule('onboarding') || state === 'forbidden') return shell(<><SalesNoAccess language={language} /><div className="text-center">{back}</div></>);
  if (state === 'notfound') {
    return shell(<Card className="p-12 text-center bg-white border border-gray-200" data-testid="onb-not-found"><p className="text-gray-700 font-medium">{ts('onb_not_found', language)}</p>{back}</Card>);
  }
  if (state === 'error') {
    return shell(
      <Card className="p-12 text-center bg-white border border-gray-200" data-testid="onb-load-error">
        <p className="text-gray-700 font-medium">{ts('onb_load_error', language)}</p>
        <div className="flex justify-center gap-2 mt-4"><Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button></div>
      </Card>,
    );
  }

  const { can, customer } = record;
  const stageIndex = ONBOARDING_STAGES.indexOf(record.stage);

  return shell(
    <div className="space-y-5" data-testid="onboarding-detail">
      <div>
        <Button asChild variant="ghost" size="sm" className="rounded-sm -ms-2 mb-2 text-gray-600">
          <Link to="/sales/onboarding"><ArrowLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('onb_back', language)}</Link>
        </Button>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-3xl md:text-4xl font-bold text-[#0A0A0A] break-words" data-testid="onboarding-title">{customer.business_name}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <OnboardingStageBadge stage={record.stage} language={language} />
              <CustomerStatusBadge status={customer.status} language={language} />
            </div>
          </div>
          <div className="flex flex-wrap gap-2" data-testid="onboarding-actions">
            {can.change_stage && record.allowed_stages.length > 0 && (
              <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => actions.stage(record)} data-testid="onboarding-stage-btn">
                <ClipboardList className="w-4 h-4 me-2" aria-hidden="true" />{ts('onb_action_stage', language)}
              </Button>
            )}
            {can.assign && (
              <Button variant="outline" className="rounded-sm" onClick={() => actions.assign(record)} data-testid="onboarding-assign-btn">
                <UserPlus className="w-4 h-4 me-2" aria-hidden="true" />{ts(record.assigned_to ? 'onb_action_reassign' : 'onb_action_assign', language)}
              </Button>
            )}
            {can.update && (
              <Button variant="outline" className="rounded-sm" onClick={() => actions.notes(record)} data-testid="onboarding-notes-btn">
                <NotebookPen className="w-4 h-4 me-2" aria-hidden="true" />{ts('onb_action_notes', language)}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* the workflow */}
      <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="onboarding-progress">
        <h2 className="text-lg font-semibold text-[#0A0A0A] mb-4">{ts('onb_section_progress', language)}</h2>
        <div className="overflow-x-auto">
          <ol className="flex items-center gap-2 min-w-max pb-1" aria-label={ts('onb_section_progress', language)}>
            {ONBOARDING_STAGES.map((stage, i) => {
              const isCurrent = stage === record.stage;
              const done = i < stageIndex;
              return (
                <li key={stage} className="flex items-center gap-2" aria-current={isCurrent ? 'step' : undefined} data-testid={`onboarding-step-${stage}`}>
                  <span className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${
                    isCurrent ? (stage === 'activated' ? 'bg-green-600 text-white border-green-600' : 'bg-[#0033A0] text-white border-[#0033A0]')
                      : done ? 'bg-blue-50 text-[#0033A0] border-blue-200' : 'bg-white text-gray-500 border-gray-200'}`}>
                    {onboardingStageName(stage, language)}
                  </span>
                  {i < ONBOARDING_STAGES.length - 1 && <span className="w-4 h-px bg-gray-300" aria-hidden="true" />}
                </li>
              );
            })}
          </ol>
        </div>
        {record.stage === 'activated' && <p className="mt-3 text-sm text-green-700">{ts('onb_reopened_hint', language)}</p>}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="onboarding-contact">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('onb_section_contact', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row label={ts('onb_field_contact', language)} testid="onb-row-contact">
              {customer.contact_name ? <bdi>{customer.contact_name}{customer.contact_position && <span className="text-gray-500"> · {customer.contact_position}</span>}</bdi> : <Empty />}
            </Row>
            <Row icon={Phone} label={ts('onb_field_phone', language)} testid="onb-row-phone">
              {customer.phone ? <a href={`tel:${customer.phone.replace(/[^\d+]/g, '')}`} className="text-[#0033A0] hover:underline"><Ltr>{customer.phone}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={MessageCircle} label={ts('onb_field_whatsapp', language)} testid="onb-row-whatsapp">
              {customer.whatsapp ? <a href={`https://wa.me/${digitsOnly(customer.whatsapp)}`} target="_blank" rel="noopener noreferrer" className="text-[#0033A0] hover:underline"><Ltr>{customer.whatsapp}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={Mail} label={ts('onb_field_email', language)} testid="onb-row-email">
              {customer.email ? <a href={`mailto:${customer.email}`} className="text-[#0033A0] hover:underline"><Ltr>{customer.email}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={MapPin} label={ts('onb_field_city', language)} testid="onb-row-city">
              {customer.city || customer.country ? [customer.city, customer.country].filter(Boolean).join(', ') : <Empty />}
            </Row>
          </div>
        </Card>

        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="onboarding-details">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('onb_section_details', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row label={ts('onb_field_owner', language)} testid="onb-row-owner">
              {record.assigned_to ? <Person person={record.assigned_to} language={language} /> : <span className="text-gray-500">{ts('onb_unassigned', language)}</span>}
            </Row>
            <Row label={ts('onb_field_status', language)} testid="onb-row-status"><CustomerStatusBadge status={customer.status} language={language} /></Row>
            <Row label={ts('onb_field_started', language)} testid="onb-row-started"><bdi>{formatDateTime(record.started_at, language)}</bdi></Row>
            <Row label={ts('onb_field_completed', language)} testid="onb-row-completed">{record.completed_at ? <bdi>{formatDateTime(record.completed_at, language)}</bdi> : <Empty />}</Row>
          </div>
          {(can.view_customer || can.view_lead) && (
            <div className="flex flex-wrap gap-2 mt-3">
              {can.view_customer && (
                <Button asChild variant="outline" size="sm" className="rounded-sm">
                  <Link to={`/sales/customers/${customer.id}`} data-testid="onboarding-view-customer"><Eye className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts('onb_view_customer', language)}</Link>
                </Button>
              )}
              {can.view_lead && record.lead_id && (
                <Button asChild variant="outline" size="sm" className="rounded-sm">
                  <Link to={`/sales/leads/${record.lead_id}`} data-testid="onboarding-view-lead">{ts('onb_view_lead', language)}</Link>
                </Button>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="onboarding-notes">
        <h2 className="text-lg font-semibold text-[#0A0A0A] mb-1">{ts('onb_section_notes', language)}</h2>
        {record.notes
          ? <p className="text-sm text-gray-700 whitespace-pre-wrap break-words" dir="auto">{record.notes}</p>
          : <p className="text-sm text-gray-400">{ts('onb_notes_empty', language)}</p>}
      </Card>

      <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="onboarding-timeline-card">
        <h2 className="text-lg font-semibold text-[#0A0A0A]">{ts('onb_section_timeline', language)}</h2>
        <p className="text-xs text-gray-500 mt-0.5 mb-4">{ts('onb_section_timeline_hint', language)}</p>
        <OnboardingTimeline onboardingId={record.id} language={language} refreshKey={timelineKey} />
      </Card>

      {actions.dialogs}
    </div>,
  );
};

export default SalesOnboardingDetail;
