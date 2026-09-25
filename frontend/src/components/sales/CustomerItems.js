import { Link } from 'react-router-dom';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { CustomerStatusBadge, OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { Person } from '@/components/sales/ActivityItems';
import { formatDate, formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { ClipboardList, Eye, NotebookPen, UserPlus } from 'lucide-react';

// How one customer / onboarding record looks - as a card (small screens) and as the pieces a table row is built from (the two
// list pages). Every button is shown only when the SERVER says the caller may do that to THIS record right now (`item.can`); the
// endpoints enforce the same rules regardless.
const ActionButton = ({ icon: Icon, labelKey, onClick, language, testid, tone = 'default' }) => (
  <Button
    size="sm" variant="outline" onClick={onClick} data-testid={testid}
    className={`rounded-sm ${tone === 'primary' ? 'border-[#0033A0] text-[#0033A0] hover:bg-blue-50' : ''}`}
  >
    <Icon className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts(labelKey, language)}
  </Button>
);

const LinkButton = ({ to, icon: Icon, labelKey, language, testid }) => (
  <Button asChild size="sm" variant="outline" className="rounded-sm">
    <Link to={to} data-testid={testid}><Icon className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts(labelKey, language)}</Link>
  </Button>
);

// ---- customers -------------------------------------------------------------------------------------------
export const CustomerName = ({ item }) => (
  <span className="min-w-0">
    <Link to={`/sales/customers/${item.id}`} className="font-medium text-[#0033A0] hover:underline break-words" data-testid={`customer-link-${item.id}`}>{item.lead.business_name}</Link>
    {item.lead.contact_name && <span className="block text-xs text-gray-500" dir="auto">{item.lead.contact_name}</span>}
  </span>
);

export const CustomerOnboardingSummary = ({ item, language }) => (item.onboarding
  ? (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <OnboardingStageBadge stage={item.onboarding.stage} language={language} />
      {item.onboarding.assigned_to && <span className="text-xs text-gray-600"><Person person={item.onboarding.assigned_to} language={language} /></span>}
    </span>
  )
  : <span className="text-gray-400">-</span>);

export const CustomerCard = ({ item, language }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`customer-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <CustomerName item={item} />
      <CustomerStatusBadge status={item.status} language={language} />
    </div>
    <p className="text-xs text-gray-600 mt-2">
      <bdi className="text-[#0A0A0A]">{item.company.name}</bdi>
      {item.company.deleted && <span className="ms-1 text-red-600">({ts('cust_company_deleted', language)})</span>}
    </p>
    <p className="text-xs text-gray-500 mt-1">
      {ts('cust_col_converted', language)}: <bdi>{formatDate(item.converted_at, language)}</bdi>
      {item.converted_by && <> · {ts('cust_converted_by', language).replace('{name}', item.converted_by.name)}</>}
    </p>
    {item.onboarding && <div className="mt-2"><CustomerOnboardingSummary item={item} language={language} /></div>}
    <div className="mt-3"><LinkButton to={`/sales/customers/${item.id}`} icon={Eye} labelKey="cust_action_open" language={language} testid={`customer-open-${item.id}`} /></div>
  </Card>
);

// ---- onboarding ------------------------------------------------------------------------------------------
export const OnboardingName = ({ item }) => (
  <span className="min-w-0">
    <Link to={`/sales/onboarding/${item.id}`} className="font-medium text-[#0033A0] hover:underline break-words" data-testid={`onboarding-link-${item.id}`}>{item.customer.business_name}</Link>
    {item.customer.contact_name && <span className="block text-xs text-gray-500" dir="auto">{item.customer.contact_name}</span>}
  </span>
);

export const OnboardingOwner = ({ item, language }) => (item.assigned_to
  ? <Person person={item.assigned_to} language={language} />
  : <span className="text-gray-500 text-sm">{ts('onb_unassigned', language)}</span>);

export const OnboardingActions = ({ item, actions, language }) => (
  <div className="flex flex-wrap gap-1">
    <LinkButton to={`/sales/onboarding/${item.id}`} icon={Eye} labelKey="onb_action_open" language={language} testid={`onboarding-open-${item.id}`} />
    {item.can.assign && <ActionButton icon={UserPlus} labelKey={item.assigned_to ? 'onb_action_reassign' : 'onb_action_assign'} tone={item.assigned_to ? 'default' : 'primary'} onClick={() => actions.assign(item)} language={language} testid={`onboarding-assign-${item.id}`} />}
    {item.can.change_stage && item.allowed_stages.length > 0 && <ActionButton icon={ClipboardList} labelKey="onb_action_stage" onClick={() => actions.stage(item)} language={language} testid={`onboarding-stage-${item.id}`} />}
    {item.can.update && <ActionButton icon={NotebookPen} labelKey="onb_action_notes" onClick={() => actions.notes(item)} language={language} testid={`onboarding-notes-${item.id}`} />}
  </div>
);

export const OnboardingCard = ({ item, actions, language }) => (
  <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`onboarding-card-${item.id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <OnboardingName item={item} />
      <OnboardingStageBadge stage={item.stage} language={language} />
    </div>
    <p className="text-xs text-gray-600 mt-2">
      {ts('onb_col_owner', language)}: <OnboardingOwner item={item} language={language} />
      <span className="text-gray-400"> · </span>
      {ts('onb_col_started', language)}: <bdi>{formatDateTime(item.started_at, language)}</bdi>
    </p>
    <div className="mt-3"><OnboardingActions item={item} actions={actions} language={language} /></div>
  </Card>
);
