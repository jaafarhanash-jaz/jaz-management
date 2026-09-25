import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import api from '@/utils/api';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react';

const PAGE_SIZE = 25;
const ALL = '__all__';

// The shared frame of the Phase-3 and Phase-4 list pages (follow-ups, calls, demos, trials, customers, onboarding): title, the VIEW tabs with their
// counts, the filters, the results (a table on wide screens, cards on narrow ones), and pagination. Each page states
// only what differs: its views (a view is a preset of server-side filters - the server does all the filtering and the
// counting), its columns, and how a row / card looks.
//
// The URL is the state (view, search, person, page, ...), so Back/Forward and links land on exactly the same view.
// `reloadRef.current` is set to a function that refetches the list and the counts - the page's action hooks call it
// after every accepted change.
const ActivityListPage = ({
  onLogout, language, setLanguage, userRole,
  moduleKey, titleKey, subtitle = null, headerExtra = null, showPerson = true,
  listPath, countsPath, views, defaultView, unwrapCounts = (c) => c,
  personParam = 'assigned_to', personLabelKey = 'filter_assigned', mineLabelKey = 'filter_assigned_me', personSource = 'leads', dimArchived = true,
  scopeHintKey = 'act_scope_hint', searchPlaceholderKey = 'act_search_placeholder',
  extraFilterKeys = [], renderExtraFilters = null,
  columns, renderRow, renderCard, emptyHintKey, dialogs = null, reloadRef = null, testid,
}) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  // who the person filter offers: the lead assignees (a manager), or - on the onboarding list - the onboarding assignees
  const canPickPerson = personSource === 'onboarding' ? can('sales.onboarding.assign') : can('sales.leads.assign');
  // (the lead sources are not used by these lists, and asking for them would be a 403 for a caller who cannot view leads)
  const ref = useSalesReference({ sources: false, assignees: personSource === 'leads' && canPickPerson, onboardingAssignees: personSource === 'onboarding' && canPickPerson });
  const [params, setParams] = useSearchParams();

  const view = views.find((v) => v.key === params.get('view')) || views.find((v) => v.key === defaultView) || views[0];
  const q = params.get('q') || '';
  const person = params.get('person') || '';
  const extra = {};
  extraFilterKeys.forEach((key) => { extra[key] = params.get(key) || ''; });
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const queryKey = params.toString();

  const [data, setData] = useState({ items: [], total: 0 });
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [searchText, setSearchText] = useState(q);
  const requestRef = useRef(0);

  const setFilter = useCallback((key, value) => {
    const next = new URLSearchParams(params);
    if (!value) next.delete(key); else next.set(key, value);
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  }, [params, setParams]);

  // the search box is debounced into the URL
  useEffect(() => {
    if (searchText === q) return undefined;
    const timer = setTimeout(() => setFilter('q', searchText.trim()), 350);
    return () => clearTimeout(timer);
  }, [searchText]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchAll = useCallback(async () => {
    const id = ++requestRef.current;
    setLoading(true);
    setError(false);
    const shared = {};
    if (q) shared.q = q;
    if (person) shared[personParam] = person;
    extraFilterKeys.forEach((key) => { if (extra[key]) shared[key] = extra[key]; });
    try {
      const [listRes, countsRes] = await Promise.all([
        api.get(listPath, { params: { ...view.params, ...shared, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } }),
        api.get(countsPath, { params: { ...(view.countParams || {}), ...shared } }),
      ]);
      if (id !== requestRef.current) return;      // a newer request superseded this one
      setData(listRes.data);
      setCounts(unwrapCounts(countsRes.data));
    } catch (e) {
      if (id === requestRef.current) setError(true);
    }
    if (id === requestRef.current) setLoading(false);
  }, [queryKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (reloadRef) reloadRef.current = fetchAll; }, [reloadRef, fetchAll]);
  useEffect(() => { if (!accessLoading && hasModule(moduleKey)) fetchAll(); }, [accessLoading, hasModule, moduleKey, fetchAll]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule(moduleKey)) return shell(<SalesNoAccess language={language} />);

  const items = data.items;
  const from = data.total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, data.total);
  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const filtersApplied = !!(q || person || extraFilterKeys.some((key) => extra[key]));
  const reset = () => { setSearchText(''); const next = new URLSearchParams(); if (view.key !== defaultView) next.set('view', view.key); setParams(next, { replace: true }); };

  const personOptions = [
    { value: 'me', label: ts(mineLabelKey, language) },
    ...(personSource === 'onboarding' ? ref.onboardingAssignees : ref.assignees).map((a) => ({ value: a.id, label: a.name })),
  ];

  return shell(
    <div className="space-y-5" data-testid={testid}>
      <div className="flex flex-wrap justify-between items-start gap-3">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid={`${testid}-title`}>{ts(titleKey, language)}</h1>
          {subtitle && <p className="text-sm text-gray-500 mt-1 max-w-2xl">{ts(subtitle, language)}</p>}
          <p className="text-sm text-gray-500 mt-1" data-testid={`${testid}-count`}>
            {loading && items.length === 0 ? ts('loading', language) : ts('leads_showing', language).replace('{from}', from).replace('{to}', to).replace('{total}', data.total)}
          </p>
        </div>
        {typeof headerExtra === 'function' ? headerExtra(counts) : headerExtra}
      </div>

      {/* the views, each with its count (the tabs ARE the status filter) */}
      <div className="overflow-x-auto" data-testid="view-tabs">
        <div role="tablist" aria-label={ts(titleKey, language)} className="flex gap-2 min-w-max pb-1">
          {views.map((v) => {
            const n = counts ? v.count(counts) : null;
            return (
              <button
                key={v.key} type="button" role="tab" aria-selected={view.key === v.key} data-testid={`view-tab-${v.key}`}
                onClick={() => { const next = new URLSearchParams(params); if (v.key === defaultView) next.delete('view'); else next.set('view', v.key); next.delete('page'); setParams(next, { replace: true }); }}
                className={`px-3 py-2 min-h-[40px] rounded-full text-sm border whitespace-nowrap ${view.key === v.key ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'}`}
              >
                {ts(v.labelKey, language)}{n !== null && <span className="ms-1.5 opacity-80" data-testid={`view-count-${v.key}`}>({n})</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* filters */}
      <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`${testid}-filters`}>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="col-span-2">
            <Label htmlFor="act-search" className="text-xs text-gray-600">{ts('filter_search', language)}</Label>
            <div className="relative mt-1">
              <Search className="w-4 h-4 text-gray-400 absolute start-3 top-1/2 -translate-y-1/2 pointer-events-none" aria-hidden="true" />
              <Input id="act-search" type="search" className="ps-9 h-10" value={searchText} onChange={(e) => setSearchText(e.target.value)}
                placeholder={ts(searchPlaceholderKey, language)} data-testid="act-search" />
            </div>
          </div>
          {showPerson && canPickPerson && (
            <div>
              <Label htmlFor="act-person" className="text-xs text-gray-600">{ts(personLabelKey, language)}</Label>
              <Select value={person || ALL} onValueChange={(v) => setFilter('person', v === ALL ? '' : v)}>
                <SelectTrigger id="act-person" className="mt-1 h-10" data-testid="act-person"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{ts('filter_all', language)}</SelectItem>
                  {personOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
          {renderExtraFilters && renderExtraFilters(extra, setFilter)}
        </div>
        <p className="text-xs text-gray-500 mt-3">{ts(scopeHintKey, language)}</p>
        {filtersApplied && (
          <div className="mt-2">
            <Button type="button" variant="ghost" size="sm" className="rounded-sm text-gray-600" onClick={reset} data-testid="filters-reset">
              <X className="w-4 h-4 me-1" aria-hidden="true" />{ts('filter_reset', language)}
            </Button>
          </div>
        )}
      </Card>

      {/* results */}
      {error ? (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid={`${testid}-error`}>
          <p className="text-gray-600 mb-4">{ts('act_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={fetchAll}>{ts('action_retry', language)}</Button>
        </Card>
      ) : loading && items.length === 0 ? (
        <div className="text-center py-12 text-gray-500" data-testid={`${testid}-loading`}>{ts('loading', language)}</div>
      ) : items.length === 0 ? (
        <Card className="p-12 text-center bg-white border border-gray-200" data-testid={`${testid}-empty`}>
          <p className="text-gray-700 font-medium">{ts(view.emptyKey, language)}</p>
          <p className="text-gray-500 text-sm mt-1">{ts(filtersApplied ? 'act_empty_filtered_hint' : emptyHintKey, language)}</p>
          {filtersApplied && <Button variant="outline" className="rounded-sm mt-4" onClick={reset}>{ts('filter_reset', language)}</Button>}
        </Card>
      ) : (
        <>
          <Card className="hidden md:block bg-white border border-gray-200 rounded-md overflow-x-auto" aria-busy={loading}>
            <Table data-testid={`${testid}-table`}>
              <caption className="sr-only">{ts(titleKey, language)}</caption>
              <TableHeader>
                <TableRow>{columns.map((c) => <TableHead key={c.labelKey} className={`text-start ${c.className || ''}`}>{ts(c.labelKey, language)}</TableHead>)}</TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id} data-testid={`${testid}-row-${item.id}`} className={dimArchived && item.lead?.archived ? 'opacity-60' : ''}>{renderRow(item)}</TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          <ul className="md:hidden space-y-3" data-testid={`${testid}-cards`}>
            {items.map((item) => <li key={item.id}>{renderCard(item)}</li>)}
          </ul>

          {pageCount > 1 && (
            <nav className="flex items-center justify-between gap-3" aria-label={ts('pagination', language)} data-testid={`${testid}-pagination`}>
              <Button variant="outline" className="rounded-sm" disabled={page <= 1 || loading} onClick={() => setFilter('page', page > 2 ? String(page - 1) : '')} data-testid="page-prev">
                <ChevronLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_prev', language)}
              </Button>
              <span className="text-sm text-gray-600">{ts('pagination_page', language).replace('{page}', page).replace('{pages}', pageCount)}</span>
              <Button variant="outline" className="rounded-sm" disabled={page >= pageCount || loading} onClick={() => setFilter('page', String(page + 1))} data-testid="page-next">
                {ts('action_next', language)}<ChevronRight className="w-4 h-4 ms-1 rtl:rotate-180" aria-hidden="true" />
              </Button>
            </nav>
          )}
        </>
      )}
      {dialogs}
    </div>,
  );
};

export default ActivityListPage;
