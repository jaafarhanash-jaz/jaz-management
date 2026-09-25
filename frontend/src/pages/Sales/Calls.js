import { useRef } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { TableCell } from '@/components/ui/table';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { CallActions, CallCard, LeadLink, NotesText, Person } from '@/components/sales/ActivityItems';
import { CallResultBadge } from '@/components/sales/ActivityBadges';
import { useCallActions } from '@/components/sales/WorkActions';
import { CALL_RESULTS, formatDuration } from '@/utils/salesActivities';
import { formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';

// Recent calls, newest first. The tabs ARE the call-result filter, each with the number of calls with that result
// (GET /calls/result-counts); the counts follow the search / person / date filters, but ignore the result itself.
const VIEWS = [
  { key: 'all', labelKey: 'filter_all', emptyKey: 'calls_empty', count: (c) => c.__total, params: { order: 'desc' } },
  ...CALL_RESULTS.map((result) => ({
    key: result, labelKey: `call_result_${result}`, emptyKey: 'calls_empty_filtered', count: (c) => c[result], params: { result, order: 'desc' },
  })),
];

const COLUMNS = [
  { labelKey: 'act_col_lead' },
  { labelKey: 'act_col_result' },
  { labelKey: 'act_col_employee' },
  { labelKey: 'act_col_called_at' },
  { labelKey: 'act_col_duration', className: 'hidden xl:table-cell' },
  { labelKey: 'act_col_actions' },
];

const SalesCalls = ({ onLogout, language, setLanguage, userRole }) => {
  const reloadRef = useRef(() => {});
  const actions = useCallActions({ language, onChanged: () => reloadRef.current() });

  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs">
        <LeadLink lead={item.lead} language={language} />
        {item.notes && <div className="mt-0.5"><NotesText notes={item.notes} language={language} /></div>}
      </TableCell>
      <TableCell><CallResultBadge result={item.result} language={language} /></TableCell>
      <TableCell className="text-sm"><Person person={item.employee} language={language} /></TableCell>
      <TableCell className="text-sm whitespace-nowrap"><bdi>{formatDateTime(item.called_at, language)}</bdi></TableCell>
      <TableCell className="text-sm hidden xl:table-cell"><bdi dir="ltr">{formatDuration(item.duration_seconds)}</bdi></TableCell>
      <TableCell><CallActions item={item} actions={actions} language={language} /></TableCell>
    </>
  );

  const dateFilter = (id, labelKey, key, extra, setFilter) => (
    <div className="col-span-2 sm:col-span-1" key={key}>
      <Label htmlFor={id} className="text-xs text-gray-600">{ts(labelKey, language)}</Label>
      <Input id={id} type="date" className="mt-1 h-10" value={extra[key]} onChange={(e) => setFilter(key, e.target.value)} data-testid={id} />
    </div>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="calls"
      moduleKey="calls" titleKey="calls_title" subtitle="calls_showing_recent" listPath="/sales/calls" countsPath="/sales/calls/result-counts"
      unwrapCounts={(data) => ({ ...data.counts, __total: data.total })}
      views={VIEWS} defaultView="all" columns={COLUMNS} renderRow={renderRow} emptyHintKey="calls_empty_hint"
      personParam="employee_id" personLabelKey="calls_filter_made_by" mineLabelKey="calls_filter_mine"
      extraFilterKeys={['called_from', 'called_to']}
      renderExtraFilters={(extra, setFilter) => (
        <>
          {dateFilter('filter-called-from', 'calls_filter_from', 'called_from', extra, setFilter)}
          {dateFilter('filter-called-to', 'calls_filter_to', 'called_to', extra, setFilter)}
        </>
      )}
      renderCard={(item) => <CallCard item={item} actions={actions} language={language} />}
      dialogs={actions.dialogs} reloadRef={reloadRef}
    />
  );
};

export default SalesCalls;
