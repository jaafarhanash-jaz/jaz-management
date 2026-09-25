import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import api from '@/utils/api';
import { validateAndFocus } from '@/utils/formValidation';
import { apiErrorMessage, fieldErrorsFrom } from '@/utils/salesErrors';
import { CAMPAIGN_STATUSES, campaignStatusName, formatDate, sourceName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { CampaignStatusBadge } from '@/components/sales/LeadBadges';
import { ChevronLeft, ChevronRight, Megaphone, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

const PAGE_SIZE = 25;
const ALL = '__all__';
const NONE = '__none__';
const EMPTY_FORM = { name: '', description: '', source: '', status: 'active', start_date: '', end_date: '' };

const formFrom = (c) => (c ? {
  name: c.name, description: c.description || '', source: c.source || '', status: c.status,
  start_date: c.start_date || '', end_date: c.end_date || '',
} : EMPTY_FORM);

const SalesCampaigns = ({ onLogout, language, setLanguage, userRole }) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  const ref = useSalesReference({});
  const canManage = can('sales.campaigns.manage');

  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [status, setStatus] = useState('');
  const [searchText, setSearchText] = useState('');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState(null);          // { campaign|null }
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const requestRef = useRef(0);
  const formRef = useRef(null);

  useEffect(() => {
    const timer = setTimeout(() => { setQ(searchText.trim()); setPage(1); }, 350);
    return () => clearTimeout(timer);
  }, [searchText]);

  const load = useCallback(async () => {
    const id = ++requestRef.current;
    setLoading(true);
    setError(false);
    try {
      const params = { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE };
      if (status) params.status = status;
      if (q) params.q = q;
      const res = await api.get('/sales/campaigns', { params });
      if (id !== requestRef.current) return;
      setData(res.data);
    } catch (e) {
      if (id === requestRef.current) setError(true);
    }
    if (id === requestRef.current) setLoading(false);
  }, [status, q, page]);

  useEffect(() => { if (!accessLoading && hasModule('campaigns')) load(); }, [accessLoading, hasModule, load]);

  const openDialog = (campaign) => { setForm(formFrom(campaign)); setErrors({}); setDialog({ campaign }); };
  // editing a field withdraws its error message (the two dates are checked against each other, so they go together)
  const setField = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    const cleared = name === 'start_date' || name === 'end_date' ? ['start_date', 'end_date'] : [name];
    setErrors((e) => (cleared.some((k) => e[k]) ? { ...e, ...Object.fromEntries(cleared.map((k) => [k, undefined])) } : e));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(formRef.current)) return;
    setSaving(true);
    setErrors({});
    const body = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      source: form.source || null,
      status: form.status,
      start_date: form.start_date || null,
      end_date: form.end_date || null,
    };
    try {
      if (dialog.campaign) await api.patch(`/sales/campaigns/${dialog.campaign.id}`, body);
      else await api.post('/sales/campaigns', body);
      toast.success(ts(dialog.campaign ? 'toast_campaign_updated' : 'toast_campaign_created', language));
      setDialog(null);
      load();
    } catch (err) {
      setErrors(fieldErrorsFrom(err, language));
      toast.error(apiErrorMessage(err, language));
    }
    setSaving(false);
  };

  const remove = async (campaign) => {
    if (!window.confirm(ts('confirm_delete_campaign', language).replace('{name}', campaign.name))) return;
    try {
      await api.delete(`/sales/campaigns/${campaign.id}`);
      toast.success(ts('toast_campaign_deleted', language));
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, language));          // e.g. "still has N lead(s)": deleting never removes leads
    }
  };

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;
  if (accessLoading) return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('campaigns')) return shell(<SalesNoAccess language={language} />);

  const items = data.items;
  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const filtersApplied = !!(status || q);
  const dates = (c) => (c.start_date || c.end_date ? `${c.start_date ? formatDate(c.start_date, language) : '…'} – ${c.end_date ? formatDate(c.end_date, language) : '…'}` : '-');
  const fieldError = (name) => (errors[name] ? <p className="text-xs text-red-600 mt-1" role="alert" data-testid={`cf-${name}-error`}>{errors[name]}</p> : null);

  return shell(
    <div className="space-y-5">
      <div className="flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-campaigns-title">{ts('campaigns_title', language)}</h1>
          {!canManage && <p className="text-sm text-gray-500 mt-1" data-testid="campaigns-view-only">{ts('campaigns_view_only', language)}</p>}
        </div>
        {canManage && (
          <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={() => openDialog(null)} data-testid="add-campaign-btn">
            <Plus className="w-4 h-4 me-2" aria-hidden="true" />{ts('campaigns_add', language)}
          </Button>
        )}
      </div>

      <Card className="p-4 bg-white border border-gray-200 rounded-md">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2">
            <Label htmlFor="campaign-search" className="text-xs text-gray-600">{ts('filter_search', language)}</Label>
            <div className="relative mt-1">
              <Search className="w-4 h-4 text-gray-400 absolute start-3 top-1/2 -translate-y-1/2 pointer-events-none" aria-hidden="true" />
              <Input id="campaign-search" type="search" className="ps-9 h-10" value={searchText} onChange={(e) => setSearchText(e.target.value)}
                placeholder={ts('campaigns_search_placeholder', language)} data-testid="campaign-search" />
            </div>
          </div>
          <div>
            <Label htmlFor="campaign-status-filter" className="text-xs text-gray-600">{ts('campaign_field_status', language)}</Label>
            <Select value={status || ALL} onValueChange={(v) => { setStatus(v === ALL ? '' : v); setPage(1); }}>
              <SelectTrigger id="campaign-status-filter" className="mt-1 h-10" data-testid="campaign-status-filter"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{ts('filter_all', language)}</SelectItem>
                {CAMPAIGN_STATUSES.map((s) => <SelectItem key={s} value={s}>{campaignStatusName(s, language)}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
      </Card>

      {error ? (
        <Card className="p-8 text-center bg-white border border-gray-200" data-testid="campaigns-error">
          <p className="text-gray-600 mb-4">{ts('campaigns_error', language)}</p>
          <Button variant="outline" className="rounded-sm" onClick={load}>{ts('action_retry', language)}</Button>
        </Card>
      ) : loading && items.length === 0 ? (
        <div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>
      ) : items.length === 0 ? (
        <Card className="p-12 text-center bg-white border border-gray-200" data-testid="campaigns-empty">
          <Megaphone className="w-12 h-12 mx-auto text-gray-300 mb-4" aria-hidden="true" />
          <p className="text-gray-700 font-medium">{ts(filtersApplied ? 'campaigns_empty_filtered' : 'campaigns_empty', language)}</p>
          {!filtersApplied && canManage && <Button className="bg-[#0033A0] hover:bg-[#002277] rounded-sm mt-4" onClick={() => openDialog(null)}>{ts('campaigns_add', language)}</Button>}
        </Card>
      ) : (
        <>
          <Card className="hidden md:block bg-white border border-gray-200 rounded-md overflow-x-auto" aria-busy={loading}>
            <Table data-testid="campaigns-table">
              <caption className="sr-only">{ts('campaigns_title', language)}</caption>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-start">{ts('campaign_field_name', language)}</TableHead>
                  <TableHead className="text-start">{ts('lead_field_source', language)}</TableHead>
                  <TableHead className="text-start">{ts('campaign_field_status', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('campaign_dates', language)}</TableHead>
                  <TableHead className="text-start">{ts('campaign_leads', language)}</TableHead>
                  <TableHead className="text-start hidden xl:table-cell">{ts('lead_created_by', language)}</TableHead>
                  {canManage && <TableHead className="text-start">{ts('team_col_actions', language)}</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((c) => (
                  <TableRow key={c.id} data-testid={`campaign-row-${c.id}`}>
                    <TableCell>
                      <span className="font-medium text-[#0A0A0A]">{c.name}</span>
                      {c.description && <div className="text-xs text-gray-500 max-w-xs truncate" title={c.description}>{c.description}</div>}
                      {(c.start_date || c.end_date) && <div className="text-xs text-gray-500 xl:hidden">{dates(c)}</div>}
                    </TableCell>
                    <TableCell className="text-sm">{c.source ? sourceName(ref.sources, c.source, language) : <span className="text-gray-400">-</span>}</TableCell>
                    <TableCell><CampaignStatusBadge status={c.status} language={language} /></TableCell>
                    <TableCell className="text-sm whitespace-nowrap hidden xl:table-cell">{dates(c)}</TableCell>
                    <TableCell className="text-sm">
                      {can('sales.leads.view') ? <Link to={`/sales/leads?campaign_id=${c.id}`} className="text-[#0033A0] hover:underline" data-testid={`campaign-leads-${c.id}`}>{c.lead_count}</Link> : c.lead_count}
                    </TableCell>
                    <TableCell className="text-sm hidden xl:table-cell">{c.created_by?.name}</TableCell>
                    {canManage && (
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          <Button size="sm" variant="outline" className="rounded-sm" onClick={() => openDialog(c)} data-testid={`edit-campaign-${c.id}`}>
                            <Pencil className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts('action_edit', language)}
                          </Button>
                          <Button size="sm" variant="outline" className="rounded-sm text-red-700 border-red-200 hover:bg-red-50" onClick={() => remove(c)} data-testid={`delete-campaign-${c.id}`}>
                            <Trash2 className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts('action_delete', language)}
                          </Button>
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          <ul className="md:hidden space-y-3" data-testid="campaigns-cards">
            {items.map((c) => (
              <li key={c.id}>
                <Card className="p-4 bg-white border border-gray-200 rounded-md">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium text-[#0A0A0A] break-words">{c.name}</p>
                      <p className="text-xs text-gray-500">{[c.source && sourceName(ref.sources, c.source, language), dates(c) !== '-' && dates(c)].filter(Boolean).join(' · ')}</p>
                    </div>
                    <CampaignStatusBadge status={c.status} language={language} />
                  </div>
                  <p className="text-sm text-gray-700 mt-2">{ts('campaign_leads', language)}: {can('sales.leads.view') ? <Link to={`/sales/leads?campaign_id=${c.id}`} className="text-[#0033A0] hover:underline">{c.lead_count}</Link> : c.lead_count}</p>
                  {canManage && (
                    <div className="flex gap-2 mt-3">
                      <Button size="sm" variant="outline" className="rounded-sm" onClick={() => openDialog(c)}>{ts('action_edit', language)}</Button>
                      <Button size="sm" variant="outline" className="rounded-sm text-red-700 border-red-200" onClick={() => remove(c)}>{ts('action_delete', language)}</Button>
                    </div>
                  )}
                </Card>
              </li>
            ))}
          </ul>

          {pageCount > 1 && (
            <nav className="flex items-center justify-between gap-3" aria-label={ts('pagination', language)}>
              <Button variant="outline" className="rounded-sm" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>
                <ChevronLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_prev', language)}
              </Button>
              <span className="text-sm text-gray-600">{ts('pagination_page', language).replace('{page}', page).replace('{pages}', pageCount)}</span>
              <Button variant="outline" className="rounded-sm" disabled={page >= pageCount || loading} onClick={() => setPage(page + 1)}>
                {ts('action_next', language)}<ChevronRight className="w-4 h-4 ms-1 rtl:rotate-180" aria-hidden="true" />
              </Button>
            </nav>
          )}
        </>
      )}

      <Dialog open={!!dialog} onOpenChange={(o) => { if (!o) setDialog(null); }}>
        <DialogContent data-testid="campaign-dialog" className="max-h-[90vh] overflow-y-auto">
          <DialogHeader className="text-start">
            <DialogTitle>{ts(dialog?.campaign ? 'campaign_edit_title' : 'campaign_create_title', language)}</DialogTitle>
            <DialogDescription>{ts('required_hint', language)}</DialogDescription>
          </DialogHeader>
          <form ref={formRef} onSubmit={submit} noValidate className="space-y-4">
            <div>
              <Label htmlFor="cf-name">{ts('campaign_field_name', language)} <span className="text-red-600" aria-hidden="true">*</span></Label>
              <Input id="cf-name" required maxLength={200} className={`mt-1 ${errors.name ? 'border-red-500' : ''}`} value={form.name}
                onChange={(e) => setField('name', e.target.value)} aria-invalid={!!errors.name} data-testid="cf-name" />
              {fieldError('name')}
            </div>
            <div>
              <Label htmlFor="cf-description">{ts('campaign_field_description', language)}</Label>
              <Textarea id="cf-description" rows={3} maxLength={2000} className="mt-1" value={form.description}
                onChange={(e) => setField('description', e.target.value)} data-testid="cf-description" />
              {fieldError('description')}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="cf-source">{ts('lead_field_source', language)}</Label>
                <Select value={form.source || NONE} onValueChange={(v) => setField('source', v === NONE ? '' : v)}>
                  <SelectTrigger id="cf-source" className="mt-1" data-testid="cf-source"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>{ts('none', language)}</SelectItem>
                    {ref.sources.map((s) => <SelectItem key={s.key} value={s.key}>{language === 'ar' ? s.name_ar : s.name_en}</SelectItem>)}
                  </SelectContent>
                </Select>
                {fieldError('source')}
              </div>
              <div>
                <Label htmlFor="cf-status">{ts('campaign_field_status', language)}</Label>
                <Select value={form.status} onValueChange={(v) => setField('status', v)}>
                  <SelectTrigger id="cf-status" className="mt-1" data-testid="cf-status"><SelectValue /></SelectTrigger>
                  <SelectContent>{CAMPAIGN_STATUSES.map((s) => <SelectItem key={s} value={s}>{campaignStatusName(s, language)}</SelectItem>)}</SelectContent>
                </Select>
                {fieldError('status')}
              </div>
              <div>
                <Label htmlFor="cf-start_date">{ts('campaign_field_start', language)}</Label>
                <Input id="cf-start_date" type="date" className="mt-1" value={form.start_date} onChange={(e) => setField('start_date', e.target.value)} data-testid="cf-start_date" />
                {fieldError('start_date')}
              </div>
              <div>
                <Label htmlFor="cf-end_date">{ts('campaign_field_end', language)}</Label>
                <Input id="cf-end_date" type="date" className={`mt-1 ${errors.end_date ? 'border-red-500' : ''}`} value={form.end_date} onChange={(e) => setField('end_date', e.target.value)}
                  aria-invalid={!!errors.end_date} data-testid="cf-end_date" />
                {fieldError('end_date')}
              </div>
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" className="rounded-sm" onClick={() => setDialog(null)}>{ts('action_cancel', language)}</Button>
              <Button type="submit" disabled={saving} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="cf-submit">{ts('action_save', language)}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>,
  );
};

export default SalesCampaigns;
