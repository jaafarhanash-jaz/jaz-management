import { useRef } from 'react';
import { TableCell } from '@/components/ui/table';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { LeadLink, NotesText, TrialActions, TrialCard, TrialFlags, When } from '@/components/sales/ActivityItems';
import { useTrialActions } from '@/components/sales/WorkActions';
import { ENDING_SOON_DAYS } from '@/utils/salesActivities';
import { formatDateTime } from '@/utils/salesLeads';

// Sales-side trials: a record that Sales offered a trial period - NOT the platform's subscription trial. Active = every
// active trial (soonest expected end first); Ending soon = active trials expected to end within a few days, INCLUDING
// ones already past their expected end (the most urgent kind); Completed and Cancelled are the finished ones.
const VIEWS = [
  { key: 'active', labelKey: 'trials_tab_active', emptyKey: 'trials_empty_active', count: (c) => c.active, params: { status: 'active', sort: 'expected_end_at', order: 'asc' } },
  { key: 'ending', labelKey: 'trials_tab_ending', emptyKey: 'trials_empty_ending', count: (c) => c.ending_soon, params: { ending_within_days: ENDING_SOON_DAYS, sort: 'expected_end_at', order: 'asc' } },
  { key: 'completed', labelKey: 'trials_tab_completed', emptyKey: 'trials_empty_completed', count: (c) => c.completed, params: { status: 'completed', sort: 'actual_end_at', order: 'desc' } },
  { key: 'cancelled', labelKey: 'trials_tab_cancelled', emptyKey: 'trials_empty_cancelled', count: (c) => c.cancelled, params: { status: 'cancelled', sort: 'actual_end_at', order: 'desc' } },
];

const COLUMNS = [
  { labelKey: 'act_col_lead' },
  { labelKey: 'act_col_started', className: 'hidden xl:table-cell' },
  { labelKey: 'act_col_expected_end' },
  { labelKey: 'act_col_status' },
  { labelKey: 'act_col_actions' },
];

const SalesTrials = ({ onLogout, language, setLanguage, userRole }) => {
  const reloadRef = useRef(() => {});
  const actions = useTrialActions({ language, onChanged: () => reloadRef.current() });

  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs">
        <LeadLink lead={item.lead} language={language} />
        {item.notes && <div className="mt-0.5"><NotesText notes={item.notes} language={language} /></div>}
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap hidden xl:table-cell"><bdi>{formatDateTime(item.started_at, language)}</bdi></TableCell>
      <TableCell className="text-sm whitespace-nowrap"><When iso={item.expected_end_at} language={language} relative={item.status === 'active'} /></TableCell>
      <TableCell>
        <TrialFlags item={item} language={language} />
        {item.actual_end_at && <span className="block text-xs text-gray-500 mt-1"><bdi>{formatDateTime(item.actual_end_at, language)}</bdi></span>}
      </TableCell>
      <TableCell><TrialActions item={item} actions={actions} language={language} /></TableCell>
    </>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="trials" showPerson={false}
      moduleKey="trials" titleKey="trials_title" subtitle="trials_subtitle" listPath="/sales/trials" countsPath="/sales/trials/counts"
      views={VIEWS} defaultView="active" columns={COLUMNS} renderRow={renderRow} emptyHintKey="trials_empty_hint"
      renderCard={(item) => <TrialCard item={item} actions={actions} language={language} />}
      dialogs={actions.dialogs} reloadRef={reloadRef}
    />
  );
};

export default SalesTrials;
