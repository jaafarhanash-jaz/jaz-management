import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { validateAndFocus } from '@/utils/formValidation';
import { fieldErrorsFrom, friendlyMessage, parseApiError } from '@/utils/salesErrors';
import { PRIORITIES, formToPayload, leadToForm, priorityName } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import DuplicateReport, { hasDuplicates } from '@/components/sales/DuplicateReport';
import { ArrowLeft } from 'lucide-react';
import { toast } from 'sonner';

const NONE = '__none__';
// The fields whose (normalized) value the server compares with other leads and with JAZ companies.
const IDENTITY_FIELDS = ['business_name', 'city', 'phone', 'whatsapp', 'email', 'website'];

// One screen for both creating and editing a lead.
//
// Duplicate handling has two layers. While the identity fields are being typed, a debounced read-only check shows a
// quiet heads-up (`live`). The server then enforces the same rule on save: a 409 carries the full report, nothing is
// written, and the person continues only by explicitly confirming (and only if the server says they may).
const SalesLeadEditor = ({ onLogout, language, setLanguage, userRole }) => {
  const { leadId } = useParams();
  const editing = !!leadId;
  const navigate = useNavigate();
  const { can, hasModule, loading: accessLoading } = useSalesAccess();
  const canAssign = can('sales.leads.assign');
  const ref = useSalesReference({ campaigns: can('sales.campaigns.view'), assignees: canAssign && !editing });

  const [original, setOriginal] = useState(null);
  const [loadState, setLoadState] = useState(editing ? 'loading' : 'ready');   // loading | ready | forbidden | notfound | error
  const [form, setForm] = useState(() => leadToForm(null));
  const [assignTo, setAssignTo] = useState('');
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [blocking, setBlocking] = useState(null);
  const [live, setLive] = useState(null);
  const formRef = useRef(null);
  const blockingRef = useRef(null);

  useEffect(() => {
    if (!editing) return undefined;
    let cancelled = false;
    setLoadState('loading');
    api.get(`/sales/leads/${leadId}`)
      .then((res) => { if (!cancelled) { setOriginal(res.data); setForm(leadToForm(res.data)); setLoadState('ready'); } })
      .catch((err) => {
        if (cancelled) return;
        const status = err.response?.status;
        setLoadState(status === 403 ? 'forbidden' : status === 404 ? 'notfound' : 'error');
      });
    return () => { cancelled = true; };
  }, [editing, leadId]);

  const setField = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
    if (IDENTITY_FIELDS.includes(name)) setBlocking(null);
  };

  // debounced, read-only duplicate check while the identity fields are typed. It previews what saving would check:
  // on an edit only the identity fields that changed (the server never re-raises a duplicate that was already
  // accepted), with the business name and city always compared as a pair.
  const identityKey = IDENTITY_FIELDS.map((f) => (form[f] || '').trim()).join('\u0001');
  useEffect(() => {
    if (loadState !== 'ready') return undefined;
    const wanted = new Set(IDENTITY_FIELDS.filter((f) => !editing || (form[f] || '').trim() !== ((original && original[f]) || '').trim()));
    if (wanted.has('business_name') || wanted.has('city')) { wanted.add('business_name'); wanted.add('city'); }
    const values = {};
    IDENTITY_FIELDS.forEach((f) => { if (wanted.has(f) && (form[f] || '').trim()) values[f] = form[f].trim(); });
    if (Object.keys(values).length === 0) { setLive(null); return undefined; }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const res = await api.post('/sales/leads/duplicate-check', { ...values, ...(editing ? { exclude_lead_id: leadId } : {}) });
        if (!cancelled) setLive(hasDuplicates(res.data) ? res.data : null);
      } catch (e) { if (!cancelled) setLive(null); }
    }, 700);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [identityKey, loadState]); // eslint-disable-line react-hooks/exhaustive-deps

  const changedPayload = useCallback(() => {
    const now = formToPayload(form);
    if (!original) return now;
    const before = formToPayload(leadToForm(original));
    const diff = {};
    Object.keys(now).forEach((k) => { if (JSON.stringify(now[k]) !== JSON.stringify(before[k])) diff[k] = now[k]; });
    return diff;
  }, [form, original]);

  const submit = async (e, confirm = false) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!confirm && formRef.current && !validateAndFocus(formRef.current)) return;
    setBusy(true);
    setErrors({});
    try {
      let res;
      if (editing) {
        const payload = changedPayload();
        if (Object.keys(payload).length === 0) {
          toast.info(ts('lead_no_changes', language));
          navigate(`/sales/leads/${leadId}`);
          return;
        }
        res = await api.patch(`/sales/leads/${leadId}`, confirm ? { ...payload, confirm_duplicates: true } : payload);
      } else {
        const body = formToPayload(form);
        if (assignTo) body.assigned_to = assignTo;
        if (confirm) body.confirm_duplicates = true;
        res = await api.post('/sales/leads', body);
      }
      toast.success(ts(editing ? 'toast_lead_updated' : 'toast_lead_created', language));
      navigate(`/sales/leads/${res.data.id}`);
      return;
    } catch (err) {
      const parsed = parseApiError(err);
      if (parsed.field === 'duplicates' && parsed.extra.duplicates) {
        setBlocking(parsed.extra.duplicates);
        setTimeout(() => blockingRef.current && blockingRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
      } else {
        const fieldErrors = fieldErrorsFrom(err, language);
        setErrors(fieldErrors);
        toast.error(friendlyMessage(parsed, language));
        const first = Object.keys(fieldErrors)[0];
        const el = first && document.getElementById(`lf-${first}`);
        if (el) el.focus();
      }
    }
    setBusy(false);
  };

  const shell = (body) => <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>{body}</Layout>;

  if (accessLoading || loadState === 'loading') return shell(<div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>);
  if (!hasModule('leads') || !can(editing ? 'sales.leads.update' : 'sales.leads.create') || loadState === 'forbidden') return shell(<SalesNoAccess language={language} />);
  if (loadState === 'notfound' || loadState === 'error') {
    return shell(
      <Card className="p-12 text-center bg-white border border-gray-200" data-testid="lead-load-error">
        <p className="text-gray-700 font-medium">{ts(loadState === 'notfound' ? 'lead_not_found' : 'lead_load_error', language)}</p>
        <Button asChild variant="outline" className="rounded-sm mt-4"><Link to="/sales/leads">{ts('action_back_to_leads', language)}</Link></Button>
      </Card>,
    );
  }
  if (editing && original && (original.archived_at || !original.can.update)) {
    return shell(
      <Card className="p-12 text-center bg-white border border-gray-200" data-testid="lead-not-editable">
        <p className="text-gray-700 font-medium">{ts(original.archived_at ? 'lead_archived_readonly' : 'lead_not_editable', language)}</p>
        <Button asChild variant="outline" className="rounded-sm mt-4"><Link to={`/sales/leads/${original.id}`}>{ts('action_back_to_lead', language)}</Link></Button>
      </Card>,
    );
  }

  const fieldError = (name) => (errors[name] ? <p className="text-xs text-red-600 mt-1" role="alert" data-testid={`lf-${name}-error`}>{errors[name]}</p> : null);
  const text = (name, { type = 'text', ltr = false, required = false, ...rest } = {}) => (
    <div key={name}>
      <Label htmlFor={`lf-${name}`}>{ts(`lead_field_${name}`, language)}{required && <span className="text-red-600" aria-hidden="true"> *</span>}</Label>
      <Input id={`lf-${name}`} type={type} required={required} dir={ltr ? 'ltr' : undefined} className={`mt-1 ${errors[name] ? 'border-red-500' : ''}`}
        style={ltr ? { textAlign: 'start' } : undefined} value={form[name]} onChange={(e) => setField(name, e.target.value)}
        aria-invalid={!!errors[name]} data-testid={`lf-${name}`} {...rest} />
      {fieldError(name)}
    </div>
  );
  const area = (name, rows = 3, max = 2000) => (
    <div key={name}>
      <Label htmlFor={`lf-${name}`}>{ts(`lead_field_${name}`, language)}</Label>
      <Textarea id={`lf-${name}`} rows={rows} maxLength={max} className={`mt-1 ${errors[name] ? 'border-red-500' : ''}`} value={form[name]}
        onChange={(e) => setField(name, e.target.value)} aria-invalid={!!errors[name]} data-testid={`lf-${name}`} />
      {fieldError(name)}
    </div>
  );
  const section = (titleKey, children, testid) => (
    <Card className="p-5 bg-white border border-gray-200 rounded-md" data-testid={testid}>
      <h2 className="text-lg font-semibold text-[#0A0A0A] mb-4">{ts(titleKey, language)}</h2>
      {children}
    </Card>
  );

  return shell(
    <div className="max-w-3xl space-y-5">
      <div>
        <Button asChild variant="ghost" size="sm" className="rounded-sm -ms-2 mb-2 text-gray-600">
          <Link to={editing ? `/sales/leads/${leadId}` : '/sales/leads'}><ArrowLeft className="w-4 h-4 me-1 rtl:rotate-180" aria-hidden="true" />{ts('action_back', language)}</Link>
        </Button>
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="lead-editor-title">{ts(editing ? 'lead_edit_title' : 'lead_create_title', language)}</h1>
        <p className="text-sm text-gray-500 mt-1">{ts('required_hint', language)}</p>
      </div>

      <form ref={formRef} onSubmit={submit} noValidate className="space-y-5" data-testid="lead-form">
        {section('lead_section_business', (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="sm:col-span-2">{text('business_name', { required: true, maxLength: 200 })}</div>
            {text('business_type', { maxLength: 100 })}
            <div className="sm:col-span-2">{area('description', 3, 2000)}</div>
          </div>
        ), 'section-business')}

        {section('lead_section_contact', (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {text('contact_name', { maxLength: 200 })}
            {text('contact_position', { maxLength: 100 })}
            {text('phone', { type: 'tel', ltr: true, maxLength: 50, autoComplete: 'off' })}
            {text('whatsapp', { type: 'tel', ltr: true, maxLength: 50, autoComplete: 'off' })}
            {text('email', { type: 'email', ltr: true, maxLength: 254, autoComplete: 'off' })}
            {text('website', { ltr: true, maxLength: 255, inputMode: 'url', autoComplete: 'off' })}
          </div>
        ), 'section-contact')}

        {section('lead_section_location', (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {text('country', { maxLength: 100 })}
            {text('city', { maxLength: 100 })}
            <div className="sm:col-span-2">{text('address', { maxLength: 500 })}</div>
            {text('latitude', { type: 'number', ltr: true, step: 'any', min: -90, max: 90 })}
            {text('longitude', { type: 'number', ltr: true, step: 'any', min: -180, max: 180 })}
          </div>
        ), 'section-location')}

        {live && !blocking && <DuplicateReport report={live} language={language} live />}

        {section('lead_section_sales', (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="lf-source">{ts('lead_field_source', language)} <span className="text-red-600" aria-hidden="true">*</span></Label>
              <Select value={form.source} onValueChange={(v) => setField('source', v)}>
                <SelectTrigger id="lf-source" className="mt-1" data-testid="lf-source" aria-required="true"><SelectValue /></SelectTrigger>
                <SelectContent>{ref.sources.map((s) => <SelectItem key={s.key} value={s.key}>{language === 'ar' ? s.name_ar : s.name_en}</SelectItem>)}</SelectContent>
              </Select>
              {fieldError('source')}
            </div>
            {can('sales.campaigns.view') && (
              <div>
                <Label htmlFor="lf-campaign_id">{ts('lead_field_campaign', language)}</Label>
                <Select value={form.campaign_id || NONE} onValueChange={(v) => setField('campaign_id', v === NONE ? '' : v)}>
                  <SelectTrigger id="lf-campaign_id" className="mt-1" data-testid="lf-campaign_id"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>{ts('none', language)}</SelectItem>
                    {ref.campaigns.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                    {/* the lead's current campaign may be completed/paused and still be listed; if it is missing, keep it selectable */}
                    {original?.campaign && !ref.campaigns.some((c) => c.id === original.campaign.id) && <SelectItem value={original.campaign.id}>{original.campaign.name}</SelectItem>}
                  </SelectContent>
                </Select>
                {fieldError('campaign_id')}
              </div>
            )}
            <div>
              <Label htmlFor="lf-priority">{ts('lead_field_priority', language)}</Label>
              <Select value={form.priority} onValueChange={(v) => setField('priority', v)}>
                <SelectTrigger id="lf-priority" className="mt-1" data-testid="lf-priority"><SelectValue /></SelectTrigger>
                <SelectContent>{PRIORITIES.map((p) => <SelectItem key={p} value={p}>{priorityName(p, language)}</SelectItem>)}</SelectContent>
              </Select>
              {fieldError('priority')}
            </div>
            {text('estimated_value', { type: 'number', ltr: true, step: '0.01', min: 0 })}
            {!editing && canAssign && (
              <div className="sm:col-span-2">
                <Label htmlFor="lf-assigned_to">{ts('lead_assign_now', language)}</Label>
                <Select value={assignTo || NONE} onValueChange={(v) => setAssignTo(v === NONE ? '' : v)}>
                  <SelectTrigger id="lf-assigned_to" className="mt-1" data-testid="lf-assigned_to"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>{ts('unassigned', language)}</SelectItem>
                    {ref.assignees.map((a) => <SelectItem key={a.id} value={a.id}>{a.name} ({ts('assign_open_leads', language).replace('{n}', a.open_leads)})</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="text-xs text-gray-500 mt-1">{ts('lead_assign_now_hint', language)}</p>
              </div>
            )}
            <div className="sm:col-span-2">{area('notes', 4, 5000)}</div>
          </div>
        ), 'section-sales')}

        {blocking && (
          <div ref={blockingRef}>
            <DuplicateReport report={blocking} language={language} onConfirm={() => submit(null, true)} busy={busy} confirmLabelKey={editing ? 'dup_confirm_save' : 'dup_confirm_create'} />
          </div>
        )}

        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={busy} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm min-h-[44px]" data-testid="lead-submit">
            {ts(editing ? 'action_save' : 'lead_create_submit', language)}
          </Button>
          <Button asChild variant="outline" className="rounded-sm min-h-[44px]">
            <Link to={editing ? `/sales/leads/${leadId}` : '/sales/leads'}>{ts('action_cancel', language)}</Link>
          </Button>
        </div>
      </form>
    </div>,
  );
};

export default SalesLeadEditor;
