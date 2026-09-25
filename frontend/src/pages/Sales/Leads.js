import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import api from '@/utils/api';
import { ts } from '@/utils/salesTranslations';
import { PRIORITIES, STAGES, formatDate, formatValue, sourceName, stageName, priorityName } from '@/utils/salesLeads';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import AssignDialog from '@/components/sales/AssignDialog';
import { PriorityBadge, StageBadge } from '@/components/sales/LeadBadges';
import { ArrowDownUp, ChevronLeft, ChevronRight, Columns3, Plus, Search, Target, UserPlus, X } from 'lucide-react';

const PAGE_SIZE = 25;
const ALL = '__all__';
const SORTS = ['created_at', 'updated_at', 'business_name', 'priority', 'estimated_value', 'pipeline_stage'];

// URL search params ARE the filter state, so Back/Forward and in-app links (the board's "view all", a campaign's lead count)
// land on exactly the same filtered view.
const FILTER_KEYS = ['q', 'pipeline_stage', 'assigned_to', 'source', 'campaign_id', 'priority', 'created_from', 'created_to', 'archived', 'sort', 'order'];
const DEFAULTS = { archived: 'exclude', sort: 'created_at', order: 'desc' };

const SalesLeads = ({ onLogout, language, setLanguage, userRole }) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  const [params, setParams] = useSearchParams();
  const ref = useSalesReference({ campaigns: can('sales.campaigns.view'), assignees: can('sales.leads.assign') });

  const filters = {};
  FILTER_KEYS.forEach((key) => { filters[key] = params.get(key) || DEFAULTS[key] || ''; });
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const queryKey = params.toString();

  const [data, setData] = useState({ items: [], total: 0 });
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [assignOpen, setAssignOpen] = useState(false);
  const [searchText, setSearchText] = useState(filters.q);
  const requestRef = useRef(0);

  const canCreate = can('sales.leads.create');
  const canAssign = can('sales.leads.assign');

  const setFilter = useCallback((key, value) => {
    const next = new URLSearchParams(params);
    if (!value || value === DEFAULTS[key]) next.delete(key); else next.set(key, value);
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  }, [params, setParams]);

  const setPage = (n) => setFilter('page', n > 1 ? String(n) : '');

  const resetFilters = () => { setSearchText(''); setParams(new URLSearchParams(), { replace: true }); };

  // debounce the search box into the URL
  useEffect(() => {
    if (searchText === filters.q) return undefined;
    const timer = setTimeout(() => setFilter('q', searchText.trim()), 350);
    return () => clearTimeout(timer);
  }, [searchText]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchLeads = useCallback(async () => {
    const id = ++requestRef.current;
    setLoading(true);
    setError(false);
    const query = {};
    FILTER_KEYS.forEach((key) => { if (filters[key] && filters[key] !== DEFAULTS[key]) query[key] = filters[key]; });
    if (filters.archived !== DEFAULTS.archived) query.archived = filters.archived;
    const { pipeline_stage: _stage, sort: _sort, order: _order, ...countQuery } = query;
    try {
      const [leadsRes, countsRes] = await Promise.all([
        api.get('/sales/leads', { params: { ...query, sort: filters.sort, order: filters.order, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE } }),
        api.get('/sales/leads/stage-counts', { params: countQuery }),
      ]);
      if (id !== requestRef.current) return;      // a newer request superseded this one
      setData(leadsRes.data);
      setCounts(countsRes.data);
      setSelected(new Set());
    } catch (e) {
      if (id === requestRef.current) setError(true);
    }
    if (id === requestRef.current) setLoading(false);
  }, [queryKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!accessLoading && hasModule('leads')) fetchLeads(); }, [accessLoading, hasModule, fetchLeads]);

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;

  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('leads')) return shell(<SalesNoAccess language={language} />);

  const items = data.items;
  const from = data.total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, data.total);
  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const filtersApplied = FILTER_KEYS.some((key) => filters[key] && filters[key] !== DEFAULTS[key]);
  const allOnPage = items.length > 0 && items.every((lead) => selected.has(lead.id));

  const toggleAll = (checked) => setSelected(checked ? new Set(items.map((lead) => lead.id)) : new Set());
  const toggleOne = (id, checked) => setSelected((prev) => { const next = new Set(prev); if (checked) next.add(id); else next.delete(id); return next; });

  const selectField = (id, label, value, onChange, options, testid, allLabel = ts('filter_all', language)) => (
    <div>
      <Label htmlFor={id} className="text-xs text-gray-600">{label}</Label>
      <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? '' : v)}>
        <SelectTrigger id={id} className="mt-1 h-10" data-testid={testid}><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{allLabel}</SelectItem>
          {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );

  const assigneeOptions = [
    { value: 'me', label: ts('filter_assigned_me', language) },
    { value: 'unassigned', label: ts('filter_unassigned', language) },
    ...ref.assignees.map((a) => ({ value: a.id, label: a.name })),
  ];

  return shell(
    <div className="space-y-5">
      <div className="flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-leads-title">{ts('leads_title', language)}</h1>
          <p className="text-sm text-gray-500 mt-1" data-testid="leads-count">
            {loading && items.length === 0 ? ts('loading', language) : ts('leads_showing', language).replace('{from}', from).replace('{to}', to).replace('{total}', data.total)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {hasModule('pipeline') && (
            <Button asChild variant="outline" className="rounded-sm">
              <Link to="/sales/pipeline"><Columns3 className="w-4 h-4 me-2" aria-hidden="true" />{ts('nav_pipeline', language)}</Link>
            </Button>
          )}
          {canCreate && (
            <Button asChild className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="add-lead-btn">
              <Link to="/sales/leads/new"><Plus className="w-4 h-4 me-2" aria-hidden="true" />{ts('leads_add', language)}</Link>
            </Button>
          )}
        </div>
      </div>

      {/* pipeline stage tabs with counts (they are the stage filter) */}
      <div className="overflow-x-auto" data-testid="stage-tabs">
        <div role="tablist" aria-label={ts('filter_stage', language)} className="flex gap-2 min-w-max pb-1">
          {[{ key: '', label: ts('filter_all', language), count: counts ? counts.total : null }, ...STAGES.map((s) => ({ key: s, label: stageName(s, language), count: counts ? counts.counts[s] : null }))].map((tab) => (
            <button
              key={tab.key || 'all'}
              type="button"
              role="tab"
              aria-selected={filters.pipeline_stage === tab.key}
              onClick={() => setFilter('pipeline_stage', tab.key)}
              data-testid={`stage-tab-${tab.key || 'all'}`}
              className={`px-3 py-2 min-h-[40px] rounded-full text-sm border whitespace-nowrap ${filters.pipeline_stage === tab.key ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'}`}
            >
              {tab.label}{tab.count !== null && <span className="ms-1.5 opacity-80">({tab.count})</span>}
            </button>
          ))}
        </div>
      </div>

      {/* filters */}
      <Card className="p-4 bg-white border border-gray-200 rounded-md" data-testid="lead-filters">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="col-span-2">
            <Label htmlFor="lead-search" className="text-xs text-gray-600">{ts('filter_search', language)}</Label>
            <div className="relative mt-1">
              <Search className="w-4 h-4 text-gray-400 absolute start-3 top-1/2 -translate-y-1/2 pointer-events-none" aria-hidden="true" />
              <Input id="lead-search" type="search" className="ps-9 h-10" value={searchText} onChange={(e) => setSearchText(e.target.value)}
                placeholder={ts('filter_search_placeholder', language)} data-testid="lead-search" />
            </div>
          </div>
          {selectField('f-assigned', ts('filter_assigned', language), filters.assigned_to, (v) => setFilter('assigned_to', v), assigneeOptions, 'filter-assigned')}
          {selectField('f-source', ts('lead_field_source', language), filters.source, (v) => setFilter('source', v), ref.sources.map((s) => ({ value: s.key, label: language === 'ar' ? s.name_ar : s.name_en })), 'filter-source')}
          {can('sales.campaigns.view') && selectField('f-campaign', ts('lead_field_campaign', language), filters.campaign_id, (v) => setFilter('campaign_id', v), ref.campaigns.map((c) => ({ value: c.id, label: c.name })), 'filter-campaign')}
          {selectField('f-priority', ts('lead_field_priority', language), filters.priority, (v) => setFilter('priority', v), PRIORITIES.map((p) => ({ value: p, label: priorityName(p, language) })), 'filter-priority')}
          <div className="col-span-2 sm:col-span-1">
            <Label htmlFor="f-from" className="text-xs text-gray-600">{ts('filter_created_from', language)}</Label>
            <Input id="f-from" type="date" className="mt-1 h-10" value={filters.created_from} onChange={(e) => setFilter('created_from', e.target.value)} data-testid="filter-created-from" />
          </div>
          <div className="col-span-2 sm:col-span-1">
            <Label htmlFor="f-to" className="text-xs text-gray-600">{ts('filter_created_to', language)}</Label>
            <Input id="f-to" type="date" className="mt-1 h-10" value={filters.created_to} onChange={(e) => setFilter('created_to', e.target.value)} data-testid="filter-created-to" />
          </div>
          <div className="col-span-2 sm:col-span-1">
            <Label htmlFor="f-sort" className="text-xs text-gray-600">{ts('filter_sort', language)}</Label>
            <div className="flex gap-2 mt-1">
              <Select value={filters.sort} onValueChange={(v) => setFilter('sort', v)}>
                <SelectTrigger id="f-sort" className="h-10" data-testid="filter-sort"><SelectValue /></SelectTrigger>
                <SelectContent>{SORTS.map((s) => <SelectItem key={s} value={s}>{ts(`sort_${s}`, language)}</SelectItem>)}</SelectContent>
              </Select>
              <Button type="button" variant="outline" className="h-10 w-10 p-0 rounded-sm shrink-0" onClick={() => setFilter('order', filters.order === 'desc' ? 'asc' : 'desc')}
                aria-label={ts(filters.order === 'desc' ? 'sort_desc' : 'sort_asc', language)} title={ts(filters.order === 'desc' ? 'sort_desc' : 'sort_asc', language)} data-testid="filter-order">
                <ArrowDownUp className={`w-4 h-4 ${filters.order === 'asc' ? 'rotate-180' : ''}`} aria-hidden="true" />
              </Button>
            </div>
          </div>
          {selectField('f-archived', ts('filter_archived', language), filters.archived === 'exclude' ? '' : filters.archived, (v) => setFilter('archived', v || 'exclude'),
            [{ value: 'only', label: ts('filter_archived_only', language) }, { value: 'all', label: ts('filter_archived_all', language) }], 'filter-archived',
            ts('filter_archived_active', language))}
        </div>
        {filtersApplied && (
          <div className="mt-3">
            <Button type="button" variant="ghost" size="sm" className="rounded-sm text-gray-600" onClick={resetFilters} data-testid="filters-reset">
              <X className="w-4 h-4 me-1" aria-hidden="true" />{ts('filter_reset', language)}
            </Button>
          </div>
        )}
      </Card>

      {/* bulk actions */}
      {canAssign && selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-blue-200 bg-blue-50 px-4 py-3" role="status" data-testid="bulk-bar">
          <span className="text-sm font-medium">{ts('bulk_selected', language).replace('{n}', selected.size)}</span>
          <Button size="sm" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => setAssignOpen(true)} data-testid="bulk-assign-btn">
            <UserPlus className="w-4 h-4 me-1" aria-hidden="true" />{ts('bulk_assign', language)}
          </Button>
          <Button size="sm" variant="ghost" className="rounded-sm" onClick={() => setSelected(new Set())}>{ts('bulk_clear', language)}</Button>
        </div>
      )}

      {/* results */}
      {error ? (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid="leads-error">
          <p className="text-gray-600 mb-4">{ts('leads_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={fetchLeads}>{ts('action_retry', language)}</Button>
        </Card>
      ) : loading && items.length === 0 ? (
        <div className="text-center py-12 text-gray-500" data-testid="leads-loading">{ts('loading', language)}</div>
      ) : items.length === 0 ? (
        <Card className="p-12 text-center bg-white border border-gray-200" data-testid="leads-empty">
          <Target className="w-12 h-12 mx-auto text-gray-300 mb-4" aria-hidden="true" />
          <p className="text-gray-700 font-medium">{ts(filtersApplied ? 'leads_empty_filtered' : 'leads_empty', language)}</p>
          <p className="text-gray-500 text-sm mt-1">{ts(filtersApplied ? 'leads_empty_filtered_hint' : 'leads_empty_hint', language)}</p>
          <div className="mt-4 flex justify-center gap-2">
            {filtersApplied && <Button variant="outline" className="rounded-sm" onClick={resetFilters}>{ts('filter_reset', language)}</Button>}
            {!filtersApplied && canCreate && (
              <Button asChild className="bg-[#0033A0] hover:bg-[#002277] rounded-sm"><Link to="/sales/leads/new">{ts('leads_add', language)}</Link></Button>
            )}
          </div>
        </Card>
      ) : (
        <>
          {/* wide screens: table */}
          <Card className="hidden md:block bg-white border border-gray-200 rounded-md overflow-x-auto" aria-busy={loading}>
            <Table data-testid="leads-table">
              <caption className="sr-only">{ts('leads_title', language)}</caption>
              <TableHeader>
                <TableRow>
                  {canAssign && (
                    <TableHead className="w-10">
                      <Checkbox checked={allOnPage} onCheckedChange={(c) => toggleAll(c === true)} aria-label={ts('bulk_select_all', language)} data-testid="select-all" />
                    </TableHead>
                  )}
                  <TableHead className="text-start">{ts('leads_col_business', language)}</TableHead>
                  <TableHead className="text-start">{ts('leads_col_contact', language)}</TableHead>
                  <TableHead className="text-start">{ts('lead_field_city', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('lead_field_source', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('lead_field_campaign', language)}</TableHead>
                  <TableHead className="text-start">{ts('lead_field_pipeline_stage', language)}</TableHead>
                  <TableHead className="text-start">{ts('lead_field_priority', language)}</TableHead>
                  <TableHead className="text-start">{ts('lead_field_assigned_to', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('lead_field_estimated_value', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('leads_col_created', language)}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((lead) => (
                  <TableRow key={lead.id} data-testid={`lead-row-${lead.id}`} className={lead.archived_at ? 'opacity-60' : ''}>
                    {canAssign && (
                      <TableCell>
                        <Checkbox checked={selected.has(lead.id)} onCheckedChange={(c) => toggleOne(lead.id, c === true)} aria-label={`${ts('bulk_select_one', language)} ${lead.business_name}`} data-testid={`select-${lead.id}`} />
                      </TableCell>
                    )}
                    <TableCell>
                      <Link to={`/sales/leads/${lead.id}`} className="font-medium text-[#0033A0] hover:underline" data-testid={`lead-link-${lead.id}`}>{lead.business_name}</Link>
                      <div className="text-xs text-gray-500">
                        {lead.business_type}
                        {lead.archived_at && <span className="ms-2 rounded-full border border-gray-300 px-2 py-0.5">{ts('lead_archived_tag', language)}</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">{lead.contact_name || <span className="text-gray-400">-</span>}</div>
                      {(lead.phone || lead.whatsapp) && <div className="text-xs text-gray-500" dir="ltr" style={{ textAlign: 'start' }}>{lead.phone || lead.whatsapp}</div>}
                    </TableCell>
                    <TableCell className="text-sm">{lead.city || <span className="text-gray-400">-</span>}</TableCell>
                    <TableCell className="text-sm hidden xl:table-cell">{sourceName(ref.sources, lead.source, language)}</TableCell>
                    <TableCell className="text-sm hidden xl:table-cell">{lead.campaign ? lead.campaign.name : <span className="text-gray-400">-</span>}</TableCell>
                    <TableCell><StageBadge stage={lead.pipeline_stage} language={language} /></TableCell>
                    <TableCell><PriorityBadge priority={lead.priority} language={language} /></TableCell>
                    <TableCell className="text-sm">
                      {lead.assigned_to ? (
                        <span>{lead.assigned_to.name}{lead.assigned_to.status === 'inactive' && <span className="ms-1 text-xs text-red-600">({ts('assignee_inactive', language)})</span>}</span>
                      ) : <span className="text-gray-400">{ts('unassigned', language)}</span>}
                    </TableCell>
                    <TableCell className="text-sm hidden xl:table-cell" dir="ltr" style={{ textAlign: 'start' }}>{formatValue(lead.estimated_value, language)}</TableCell>
                    <TableCell className="text-sm whitespace-nowrap hidden xl:table-cell">{formatDate(lead.created_at, language)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          {/* narrow screens: cards */}
          <ul className="md:hidden space-y-3" data-testid="leads-cards">
            {items.map((lead) => (
              <li key={lead.id}>
                <Card className={`p-4 bg-white border border-gray-200 rounded-md ${lead.archived_at ? 'opacity-60' : ''}`}>
                  <div className="flex items-start gap-3">
                    {canAssign && (
                      <Checkbox className="mt-1" checked={selected.has(lead.id)} onCheckedChange={(c) => toggleOne(lead.id, c === true)} aria-label={`${ts('bulk_select_one', language)} ${lead.business_name}`} />
                    )}
                    <div className="min-w-0 flex-1">
                      <Link to={`/sales/leads/${lead.id}`} className="font-medium text-[#0033A0] hover:underline">{lead.business_name}</Link>
                      <p className="text-xs text-gray-500">{[lead.business_type, lead.city].filter(Boolean).join(' · ')}</p>
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        <StageBadge stage={lead.pipeline_stage} language={language} />
                        <PriorityBadge priority={lead.priority} language={language} />
                      </div>
                      <p className="text-xs text-gray-600 mt-2">{lead.assigned_to ? lead.assigned_to.name : ts('unassigned', language)} · {sourceName(ref.sources, lead.source, language)}</p>
                      {lead.phone && <p className="text-xs text-gray-500 mt-1" dir="ltr" style={{ textAlign: 'start' }}>{lead.phone}</p>}
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>

          {/* pagination */}
          {pageCount > 1 && (
            <nav className="flex items-center justify-between gap-3" aria-label={ts('pagination', language)} data-testid="leads-pagination">
              <Button variant="outline" className="rounded-sm" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)} data-testid="page-prev">
                <ChevronLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_prev', language)}
              </Button>
              <span className="text-sm text-gray-600">{ts('pagination_page', language).replace('{page}', page).replace('{pages}', pageCount)}</span>
              <Button variant="outline" className="rounded-sm" disabled={page >= pageCount || loading} onClick={() => setPage(page + 1)} data-testid="page-next">
                {ts('action_next', language)}<ChevronRight className="w-4 h-4 ms-1 rtl:rotate-180" aria-hidden="true" />
              </Button>
            </nav>
          )}
        </>
      )}

      <AssignDialog open={assignOpen} onOpenChange={setAssignOpen} leadIds={[...selected]} language={language} onDone={fetchLeads} />
    </div>
  );
};

export default SalesLeads;
