import { useRef } from 'react';
import { TableCell } from '@/components/ui/table';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { DemoActions, DemoCard, DemoFlags, LeadLink, NotesText, Person, When } from '@/components/sales/ActivityItems';
import { useDemoActions } from '@/components/sales/WorkActions';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { AlertTriangle } from 'lucide-react';

// Upcoming = every demo still `scheduled` (soonest first). One whose time has passed stays here, flagged "past due", until
// somebody records its outcome - so no demo can slip out of every tab. Completed, and Cancelled / No-show, are the others.
const VIEWS = [
  { key: 'upcoming', labelKey: 'demos_tab_upcoming', emptyKey: 'demos_empty_upcoming', count: (c) => c.scheduled, params: { status: 'scheduled', order: 'asc' } },
  { key: 'completed', labelKey: 'demos_tab_completed', emptyKey: 'demos_empty_completed', count: (c) => c.completed, params: { status: 'completed', order: 'desc' } },
  { key: 'closed', labelKey: 'demos_tab_closed', emptyKey: 'demos_empty_closed', count: (c) => c.cancelled + c.no_show, params: { status: 'cancelled,no_show', order: 'desc' } },
];

const COLUMNS = [
  { labelKey: 'act_col_lead' },
  { labelKey: 'act_col_when' },
  { labelKey: 'act_col_assignee' },
  { labelKey: 'act_col_status' },
  { labelKey: 'act_col_actions' },
];

const SalesDemos = ({ onLogout, language, setLanguage, userRole }) => {
  const reloadRef = useRef(() => {});
  const actions = useDemoActions({ language, onChanged: () => reloadRef.current() });

  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs">
        <LeadLink lead={item.lead} language={language} />
        {item.notes && <div className="mt-0.5"><NotesText notes={item.notes} language={language} /></div>}
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap"><When iso={item.scheduled_at} language={language} relative={item.status === 'scheduled'} /></TableCell>
      <TableCell className="text-sm"><Person person={item.assigned_to} language={language} /></TableCell>
      <TableCell>
        <DemoFlags item={item} language={language} />
        {item.completed_at && <span className="block text-xs text-gray-500 mt-1"><bdi>{formatDateTime(item.completed_at, language)}</bdi></span>}
      </TableCell>
      <TableCell><DemoActions item={item} actions={actions} language={language} /></TableCell>
    </>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="demos"
      moduleKey="demos" titleKey="demos_title" listPath="/sales/demos" countsPath="/sales/demos/counts"
      views={VIEWS} defaultView="upcoming" columns={COLUMNS} renderRow={renderRow} emptyHintKey="demos_empty_hint"
      renderCard={(item) => <DemoCard item={item} actions={actions} language={language} />}
      headerExtra={(counts) => (counts && counts.past_due > 0 ? (
        <p className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" role="status" data-testid="demos-past-due">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />{ts('act_needs_outcome', language).replace('{n}', counts.past_due)}
        </p>
      ) : null)}
      dialogs={actions.dialogs} reloadRef={reloadRef}
    />
  );
};

export default SalesDemos;
