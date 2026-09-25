import { useRef } from 'react';
import { TableCell } from '@/components/ui/table';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { OnboardingStageBadge } from '@/components/sales/CustomerBadges';
import { OnboardingActions, OnboardingCard, OnboardingName, OnboardingOwner } from '@/components/sales/CustomerItems';
import { useOnboardingActions } from '@/components/sales/OnboardingDialogs';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { ONBOARDING_STAGES, onboardingStageName } from '@/utils/salesCustomers';
import { formatDate } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';

// Onboarding: what a new customer goes through from won to activated. The tabs are presets of server-side filters, each with its
// number (GET /onboarding/counts). What a caller sees is decided by the ONBOARDING scope: a Sales Manager sees every record, an
// Onboarding Employee only the ones assigned to them (so for them "My onboarding" is the whole list and there is no
// "Unassigned" tab to show).
const ALL = '__all__';

const TAB = (key, labelKey, emptyKey, count, params) => ({ key, labelKey, emptyKey, count, params });

const COLUMNS = [
  { labelKey: 'onb_col_customer' },
  { labelKey: 'onb_col_stage' },
  { labelKey: 'onb_col_owner' },
  { labelKey: 'onb_col_started', className: 'hidden xl:table-cell' },
  { labelKey: 'onb_col_actions' },
];

const SalesOnboarding = ({ onLogout, language, setLanguage, userRole }) => {
  const { can } = useSalesAccess();
  const reloadRef = useRef(() => {});
  const actions = useOnboardingActions({ language, onChanged: () => reloadRef.current() });
  const seesAll = can('sales.onboarding.scope_all');

  const views = seesAll
    ? [
      TAB('open', 'onb_tab_open', 'onb_empty_open', (c) => c.open, { open: true }),
      TAB('mine', 'onb_tab_mine', 'onb_empty_mine', (c) => c.mine, { assigned_to: 'me', open: true }),
      TAB('unassigned', 'onb_tab_unassigned', 'onb_empty_unassigned', (c) => c.unassigned, { unassigned: true }),
      TAB('activated', 'onb_tab_activated', 'onb_empty_activated', (c) => c.stages.activated, { stage: 'activated' }),
      TAB('all', 'onb_tab_all', 'onb_empty_all', (c) => c.total, {}),
    ]
    : [
      TAB('mine', 'onb_tab_mine', 'onb_empty_mine', (c) => c.open, { open: true }),
      TAB('activated', 'onb_tab_activated', 'onb_empty_activated', (c) => c.stages.activated, { stage: 'activated' }),
      TAB('all', 'onb_tab_all', 'onb_empty_all', (c) => c.total, {}),
    ];

  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs"><OnboardingName item={item} /></TableCell>
      <TableCell><OnboardingStageBadge stage={item.stage} language={language} /></TableCell>
      <TableCell className="text-sm"><OnboardingOwner item={item} language={language} /></TableCell>
      <TableCell className="text-sm whitespace-nowrap hidden xl:table-cell"><bdi>{formatDate(item.started_at, language)}</bdi></TableCell>
      <TableCell><OnboardingActions item={item} actions={actions} language={language} /></TableCell>
    </>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="onboarding" personSource="onboarding" dimArchived={false}
      moduleKey="onboarding" titleKey="onb_title" subtitle="onb_subtitle" listPath="/sales/onboarding" countsPath="/sales/onboarding/counts"
      personParam="assigned_to" personLabelKey="onb_filter_owner" mineLabelKey="onb_filter_owner_me"
      scopeHintKey="onb_scope_hint" searchPlaceholderKey="onb_search_placeholder"
      views={views} defaultView={seesAll ? 'open' : 'mine'} columns={COLUMNS} renderRow={renderRow} emptyHintKey="onb_empty_hint"
      extraFilterKeys={['stage']}
      renderExtraFilters={(extra, setFilter) => (
        <div className="col-span-2 sm:col-span-1">
          <Label htmlFor="onb-filter-stage" className="text-xs text-gray-600">{ts('onb_filter_stage', language)}</Label>
          <Select value={extra.stage || ALL} onValueChange={(v) => setFilter('stage', v === ALL ? '' : v)}>
            <SelectTrigger id="onb-filter-stage" className="mt-1 h-10" data-testid="onb-filter-stage"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{ts('filter_all', language)}</SelectItem>
              {ONBOARDING_STAGES.map((stage) => <SelectItem key={stage} value={stage}>{onboardingStageName(stage, language)}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      )}
      renderCard={(item) => <OnboardingCard item={item} actions={actions} language={language} />}
      dialogs={actions.dialogs} reloadRef={reloadRef}
    />
  );
};

export default SalesOnboarding;
