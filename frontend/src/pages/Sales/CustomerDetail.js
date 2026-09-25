import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { CustomerStatusBadge, OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { Empty, Ltr, Row } from '@/components/sales/DetailRow';
import { Person } from '@/components/sales/ActivityItems';
import { ArrowLeft, Building2, ClipboardCheck, Mail, MapPin, Phone, Target } from 'lucide-react';

// One customer: the JAZ company it became, the contact details Sales collected on the lead, the conversion, and (only to a
// caller who may view onboarding) where its onboarding stands. Read-only - a customer's status is not edited by hand: it follows
// its onboarding stage. The links to the lead and to the onboarding follow what the server says the caller may open.
const SalesCustomerDetail = ({ onLogout, language, setLanguage, userRole }) => {
  const { customerId } = useParams();
  const { hasModule, loading: accessLoading } = useSalesAccess();
  const [customer, setCustomer] = useState(null);
  const [state, setState] = useState('loading');     // loading | ready | forbidden | notfound | error

  const load = useCallback(async () => {
    setState('loading');
    try {
      const res = await api.get(`/sales/customers/${customerId}`);
      setCustomer(res.data);
      setState('ready');
    } catch (err) {
      const status = err.response?.status;
      setState(status === 403 ? 'forbidden' : status === 404 ? 'notfound' : 'error');
    }
  }, [customerId]);

  useEffect(() => { if (!accessLoading && hasModule('customers')) load(); }, [accessLoading, hasModule, load]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  const back = <Button asChild variant="outline" className="rounded-sm mt-4"><Link to="/sales/customers">{ts('cust_back', language)}</Link></Button>;

  if (accessLoading || state === 'loading') return shell(<div className="text-center py-12 text-gray-500" data-testid="customer-loading">{ts('loading', language)}</div>);
  if (!hasModule('customers') || state === 'forbidden') return shell(<><SalesNoAccess language={language} /><div className="text-center">{back}</div></>);
  if (state === 'notfound') {
    return shell(<Card className="p-12 text-center bg-white border border-gray-200" data-testid="customer-not-found"><p className="text-gray-700 font-medium">{ts('cust_not_found', language)}</p>{back}</Card>);
  }
  if (state === 'error') {
    return shell(
      <Card className="p-12 text-center bg-white border border-gray-200" data-testid="customer-load-error">
        <p className="text-gray-700 font-medium">{ts('cust_load_error', language)}</p>
        <div className="flex justify-center gap-2 mt-4"><Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button></div>
      </Card>,
    );
  }

  const { lead, company, onboarding, can } = customer;

  return shell(
    <div className="space-y-5" data-testid="customer-detail">
      <div>
        <Button asChild variant="ghost" size="sm" className="rounded-sm -ms-2 mb-2 text-gray-600">
          <Link to="/sales/customers"><ArrowLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('cust_back', language)}</Link>
        </Button>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-3xl md:text-4xl font-bold text-[#0A0A0A] break-words" data-testid="customer-title">{lead.business_name}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <CustomerStatusBadge status={customer.status} language={language} />
              {lead.archived && <span className="text-xs rounded-full border border-gray-300 px-2 py-0.5 text-gray-600">{ts('cust_archived_lead', language)}</span>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2" data-testid="customer-actions">
            {can.view_lead && (
              <Button asChild variant="outline" className="rounded-sm">
                <Link to={`/sales/leads/${lead.id}`} data-testid="customer-open-lead"><Target className="w-4 h-4 me-2" aria-hidden="true" />{ts('cust_open_lead', language)}</Link>
              </Button>
            )}
            {can.view_onboarding && onboarding && (
              <Button asChild className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
                <Link to={`/sales/onboarding/${onboarding.id}`} data-testid="customer-open-onboarding"><ClipboardCheck className="w-4 h-4 me-2" aria-hidden="true" />{ts('cust_open_onboarding', language)}</Link>
              </Button>
            )}
          </div>
        </div>
      </div>

      {company.deleted && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="status" data-testid="customer-company-deleted">{ts('cust_company_deleted', language)}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="customer-company">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('cust_section_company', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row icon={Building2} label={ts('cust_field_company', language)} testid="cust-row-company"><bdi>{company.name}</bdi></Row>
            <Row label={ts('cust_field_subscription', language)} testid="cust-row-subscription">
              {company.subscription_status ? ts(`sub_status_${company.subscription_status}`, language) : <Empty />}
            </Row>
            <Row label={ts('cust_field_owner', language)} testid="cust-row-owner">{company.owner_name ? <bdi>{company.owner_name}</bdi> : <Empty />}</Row>
            <Row icon={Mail} label={ts('cust_field_owner_email', language)} testid="cust-row-owner-email">{company.owner_email ? <Ltr>{company.owner_email}</Ltr> : <Empty />}</Row>
            <Row icon={Phone} label={ts('cust_field_owner_phone', language)} testid="cust-row-owner-phone">{company.owner_phone ? <Ltr>{company.owner_phone}</Ltr> : <Empty />}</Row>
            <Row label={ts('cust_field_employees', language)} testid="cust-row-employees">{company.employee_count ?? <Empty />}</Row>
          </div>
        </Card>

        <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="customer-contact">
          <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('cust_section_contact', language)}</h2>
          <div className="divide-y divide-gray-100">
            <Row label={ts('cust_field_contact', language)} testid="cust-row-contact">{lead.contact_name ? <bdi>{lead.contact_name}</bdi> : <Empty />}</Row>
            <Row icon={Phone} label={ts('cust_field_phone', language)} testid="cust-row-phone">
              {lead.phone ? <a href={`tel:${lead.phone.replace(/[^\d+]/g, '')}`} className="text-[#0033A0] hover:underline"><Ltr>{lead.phone}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={Mail} label={ts('cust_field_email', language)} testid="cust-row-email">
              {lead.email ? <a href={`mailto:${lead.email}`} className="text-[#0033A0] hover:underline"><Ltr>{lead.email}</Ltr></a> : <Empty />}
            </Row>
            <Row icon={MapPin} label={ts('cust_field_city', language)} testid="cust-row-city">{lead.city || <Empty />}</Row>
          </div>
        </Card>

        <div className="space-y-5">
          <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="customer-conversion">
            <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('cust_section_conversion', language)}</h2>
            <div className="divide-y divide-gray-100">
              <Row label={ts('cust_field_converted_at', language)} testid="cust-row-converted-at"><bdi>{formatDateTime(customer.converted_at, language)}</bdi></Row>
              <Row label={ts('cust_field_converted_by', language)} testid="cust-row-converted-by">{customer.converted_by ? <Person person={customer.converted_by} language={language} /> : <Empty />}</Row>
            </div>
          </Card>

          <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid="customer-onboarding">
            <h2 className="text-lg font-semibold text-[#0A0A0A] mb-2">{ts('cust_section_onboarding', language)}</h2>
            {onboarding ? (
              <div className="divide-y divide-gray-100">
                <Row label={ts('onb_col_stage', language)} testid="cust-row-stage"><OnboardingStageBadge stage={onboarding.stage} language={language} /></Row>
                <Row label={ts('cust_onboarding_owner', language)} testid="cust-row-onboarding-owner">
                  {onboarding.assigned_to ? <Person person={onboarding.assigned_to} language={language} /> : <span className="text-gray-500">{ts('onb_unassigned', language)}</span>}
                </Row>
              </div>
            ) : (
              <p className="text-sm text-gray-500" data-testid="customer-no-onboarding">{ts('cust_no_onboarding_access', language)}</p>
            )}
          </Card>
        </div>
      </div>
    </div>,
  );
};

export default SalesCustomerDetail;
