import { useRef } from 'react';
import { TableCell } from '@/components/ui/table';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { FollowupActions, FollowupCard, FollowupFlags, LeadLink, NotesText, Person, When } from '@/components/sales/ActivityItems';
import { useFollowupActions } from '@/components/sales/WorkActions';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';

// The views are presets of server-side filters: My follow-ups = assigned to me and pending; Upcoming = pending and not yet
// due; Overdue = pending and already due (due_at < now); Completed. The server filters and counts; this page only asks.
const VIEWS = [
  { key: 'mine', labelKey: 'followups_tab_mine', emptyKey: 'followups_empty_mine', count: (c) => c.mine_pending, params: { assigned_to: 'me', status: 'pending', sort: 'due_at', order: 'asc' } },
  { key: 'upcoming', labelKey: 'followups_tab_upcoming', emptyKey: 'followups_empty_upcoming', count: (c) => c.upcoming, params: { due: 'upcoming', sort: 'due_at', order: 'asc' } },
  { key: 'overdue', labelKey: 'followups_tab_overdue', emptyKey: 'followups_empty_overdue', count: (c) => c.overdue, params: { due: 'overdue', sort: 'due_at', order: 'asc' } },
  { key: 'completed', labelKey: 'followups_tab_completed', emptyKey: 'followups_empty_completed', count: (c) => c.completed, params: { status: 'completed', sort: 'completed_at', order: 'desc' } },
];

const COLUMNS = [
  { labelKey: 'act_col_lead' },
  { labelKey: 'act_col_due' },
  { labelKey: 'act_col_assignee' },
  { labelKey: 'act_col_status' },
  { labelKey: 'act_col_actions' },
];

const SalesFollowups = ({ onLogout, language, setLanguage, userRole }) => {
  const reloadRef = useRef(() => {});
  const actions = useFollowupActions({ language, onChanged: () => reloadRef.current() });

  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs">
        <LeadLink lead={item.lead} language={language} />
        <div className="mt-0.5"><NotesText notes={item.notes} language={language} /></div>
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap"><When iso={item.due_at} language={language} relative={item.status === 'pending'} /></TableCell>
      <TableCell className="text-sm"><Person person={item.assigned_to} language={language} /></TableCell>
      <TableCell>
        <FollowupFlags item={item} language={language} />
        {item.completed_at && <span className="block text-xs text-gray-500 mt-1"><bdi>{formatDateTime(item.completed_at, language)}</bdi></span>}
      </TableCell>
      <TableCell><FollowupActions item={item} actions={actions} language={language} /></TableCell>
    </>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="followups"
      moduleKey="followups" titleKey="followups_title" listPath="/sales/followups" countsPath="/sales/followups/counts"
      views={VIEWS} defaultView="mine" columns={COLUMNS} renderRow={renderRow} emptyHintKey="followups_empty_hint"
      renderCard={(item) => <FollowupCard item={item} actions={actions} language={language} />}
      dialogs={actions.dialogs} reloadRef={reloadRef}
    />
  );
};

export default SalesFollowups;
