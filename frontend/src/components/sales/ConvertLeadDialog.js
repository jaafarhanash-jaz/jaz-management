import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ActivityFormDialog, FieldError, splitErrors } from '@/components/sales/ActivityFields';
import api from '@/utils/api';
import { FORM_FIELDS, formToPreflight, generatePassword, planLabel, valuesToForm } from '@/utils/salesCustomers';
import { parseApiError } from '@/utils/salesErrors';
import { ts } from '@/utils/salesTranslations';
import { Copy, Eye, EyeOff, Sparkles, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';

// Convert a WON lead into a customer and a JAZ company. The flow, all decided by the server:
//   1. opening the dialog asks POST /leads/{id}/convert/preflight, which returns the values the company would be created from
//      (the lead's business name, contact, email, phone, address), what would stop the conversion, any JAZ company it could
//      duplicate, and the subscription plans on offer;
//   2. the person may edit the values; leaving the name / email / phone fields asks the preflight again;
//   3. an exact duplicate (an existing company for the same owner email / phone) or a taken email / phone BLOCKS - the button
//      stays off until it is changed; a possible duplicate needs an explicit "convert anyway" confirmation;
//   4. POST /leads/{id}/convert creates the company through the platform's own company service, the customer and its
//      onboarding in one transaction. It is idempotent: a lead that was converted meanwhile answers with the existing
//      customer and creates nothing.
// The owner's initial password is typed (or generated) here, sent once, and never shown again by the server.
const REQUEST_FIELDS = ['business_name', 'owner_name', 'owner_email', 'owner_phone', 'owner_password', 'subscription_plan_id', 'address'];
const EMPTY = { business_name: '', owner_name: '', owner_email: '', owner_phone: '', address: '', subscription_plan_id: '', owner_password: '' };

const KIND_STYLES = { exact: 'bg-red-100 text-red-800 border-red-200', possible: 'bg-amber-100 text-amber-900 border-amber-200' };

const CompanyMatches = ({ duplicates, language }) => (
  <ul className="mt-2 space-y-2" data-testid="conv-duplicate-list">
    {duplicates.companies.map((company) => (
      <li key={company.id} className="rounded-md bg-white/70 border border-gray-200 p-2.5" data-testid={`conv-duplicate-${company.id}`}>
        <p className="text-sm font-medium text-[#0A0A0A]" dir="auto">{company.name}</p>
        <ul className="flex flex-wrap gap-1.5 mt-1">
          {company.matches.map((m, i) => (
            <li key={`${m.field}-${m.matched_on}-${i}`} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${KIND_STYLES[m.kind] || KIND_STYLES.possible}`}>
              <span className="font-semibold">{ts(`dup_kind_${m.kind}`, language)}</span>
              <span aria-hidden="true">·</span>
              <span>{ts(`dup_r_${m.field}_${m.matched_on}`, language)}</span>
            </li>
          ))}
        </ul>
      </li>
    ))}
  </ul>
);

const ConvertLeadDialog = ({ open, onOpenChange, lead, language, onDone }) => {
  const [pre, setPre] = useState(null);                  // the latest preflight; null = loading
  const [form, setForm] = useState(EMPTY);
  const [confirm, setConfirm] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [checking, setChecking] = useState(false);
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!open) { setForm(EMPTY); setPre(null); return undefined; }      // the password never outlives the dialog
    let cancelled = false;
    setPre(null);
    setForm(EMPTY);
    setConfirm(false);
    setShowPassword(false);
    setErrors({});
    setGeneral('');
    api.post(`/sales/leads/${lead.id}/convert/preflight`, {})
      .then((res) => {
        if (cancelled) return;
        setPre(res.data);
        setForm({ ...EMPTY, ...valuesToForm(res.data.values), subscription_plan_id: res.data.plans[0]?.id || '' });
      })
      .catch((err) => { if (!cancelled) { setPre(false); setGeneral(parseApiError(err).message || ts('toast_error', language)); } });
    return () => { cancelled = true; };
  }, [open, lead?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };

  // Ask the server again about what is typed now (the name, email and phone decide the duplicates and the taken-email checks).
  const recheck = async (next) => {
    const id = ++requestRef.current;
    setChecking(true);
    try {
      const res = await api.post(`/sales/leads/${lead.id}/convert/preflight`, formToPreflight(next));
      if (id !== requestRef.current) return;
      setPre(res.data);
      if (!res.data.requires_confirmation) setConfirm(false);
    } catch (err) {
      // the form stays usable: the conversion itself validates everything again
    }
    if (id === requestRef.current) setChecking(false);
  };

  const blockersFor = (field) => (pre?.blockers || []).filter((b) => b.field === field);
  const blocked = !!pre && pre.blockers.length > 0;
  const noPlans = !!pre && pre.plans.length === 0;
  const needsConfirm = !!pre && (pre.requires_confirmation || (pre.duplicates.has_possible && !pre.duplicates.has_exact));

  const copyPassword = async () => {
    try {
      await navigator.clipboard.writeText(form.owner_password);
      toast.success(ts('toast_password_copied', language));
    } catch (e) {
      // clipboard access can be refused; the password stays visible for a manual copy
      setShowPassword(true);
    }
  };

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setGeneral('');
    const body = {
      business_name: form.business_name.trim(), owner_name: form.owner_name.trim(), owner_email: form.owner_email.trim(),
      owner_phone: form.owner_phone.trim(), owner_password: form.owner_password, subscription_plan_id: form.subscription_plan_id,
      confirm_duplicates: confirm,
    };
    if (form.address.trim()) body.address = form.address.trim();
    try {
      const res = await api.post(`/sales/leads/${lead.id}/convert`, body);
      toast.success(ts(res.data.already_converted ? 'toast_already_converted' : 'toast_converted', language));
      setForm(EMPTY);
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      const parsed = parseApiError(err);
      if (parsed.extra?.duplicates) {          // duplicates_found / company_exists: show what it collides with
        setPre((p) => ({ ...p, duplicates: parsed.extra.duplicates, requires_confirmation: parsed.code === 'duplicates_found' }));
      }
      const split = splitErrors(err, language, REQUEST_FIELDS);
      setErrors(split.fields);
      setGeneral(split.general);
      if (parsed.code === 'duplicates_found') setGeneral('');
    }
    setBusy(false);
  };

  const inputProps = (name) => ({
    id: `conv-${name}`, value: form[name], 'data-testid': `conv-${name}`,
    onChange: (e) => set(name, e.target.value), 'aria-invalid': !!(errors[name] || blockersFor(name).length),
    className: `mt-1 ${errors[name] || blockersFor(name).length ? 'border-red-500' : ''}`,
  });
  const fieldNote = (name) => (errors[name] ? null : blockersFor(name).map((b) => (
    <p key={b.code} className="text-xs text-red-600 mt-1" role="alert" data-testid={`conv-${name}-blocker`}>{ts(`conv_blocker_${b.code}`, language)}</p>
  )));

  const loading = pre === null && open;
  const submitDisabled = loading || pre === false || pre?.already_converted || blocked || noPlans || checking || (needsConfirm && !confirm);
  const otherBlockers = (pre?.blockers || []).filter((b) => !FORM_FIELDS.includes(b.field));
  // the business name is free text of any script: isolate its direction so it never reorders the sentence around it
  const [titleBefore, titleAfter] = ts('conv_title', language).split('{name}');

  return (
    <ActivityFormDialog
      open={open} onOpenChange={onOpenChange} language={language} testid="convert-dialog" busy={busy} error={general} onSubmit={submit}
      title={<>{titleBefore}<bdi>{lead?.business_name}</bdi>{titleAfter}</>} description={ts('conv_hint', language)}
      submitLabel={ts('conv_submit', language)} submitDisabled={submitDisabled}
    >
      {loading && <p className="text-sm text-gray-500 py-6 text-center" data-testid="conv-loading">{ts('conv_loading', language)}</p>}

      {pre && (
        <>
          {otherBlockers.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3" role="alert" data-testid="conv-blockers">
              <p className="text-sm font-semibold text-red-800">{ts('conv_blockers_title', language)}</p>
              <ul className="mt-1 list-disc ps-5 text-sm text-red-800">
                {otherBlockers.map((b) => <li key={b.code}>{ts(`conv_blocker_${b.code}`, language)}</li>)}
              </ul>
            </div>
          )}

          {pre.duplicates.companies.length > 0 && (
            <div className={`rounded-md border p-3 ${pre.duplicates.has_exact ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`} role="alert" data-testid="conv-duplicates">
              <div className="flex items-start gap-2.5">
                <TriangleAlert className={`w-5 h-5 mt-0.5 shrink-0 ${pre.duplicates.has_exact ? 'text-red-600' : 'text-amber-600'}`} aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-[#0A0A0A]" data-testid="conv-duplicates-title">
                    {ts(pre.duplicates.has_exact ? 'conv_duplicates_exact_title' : 'conv_duplicates_possible_title', language)}
                  </p>
                  <p className="text-sm text-gray-700 mt-0.5">{ts(pre.duplicates.has_exact ? 'conv_duplicates_exact_hint' : 'conv_duplicates_possible_hint', language)}</p>
                  <CompanyMatches duplicates={pre.duplicates} language={language} />
                </div>
              </div>
            </div>
          )}

          <div>
            <Label htmlFor="conv-business_name">{ts('conv_field_business_name', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
            <Input {...inputProps('business_name')} required maxLength={200} dir="auto" onBlur={() => recheck(form)} />
            <FieldError id="conv-business_name" message={errors.business_name} />
          </div>
          <div>
            <Label htmlFor="conv-owner_name">{ts('conv_field_owner_name', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
            <Input {...inputProps('owner_name')} required maxLength={200} dir="auto" />
            <FieldError id="conv-owner_name" message={errors.owner_name} />
            {fieldNote('owner_name')}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <Label htmlFor="conv-owner_email">{ts('conv_field_owner_email', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
              <Input {...inputProps('owner_email')} type="email" required dir="ltr" style={{ textAlign: 'start' }} onBlur={() => recheck(form)} autoComplete="off" />
              <FieldError id="conv-owner_email" message={errors.owner_email} />
              {fieldNote('owner_email')}
            </div>
            <div>
              <Label htmlFor="conv-owner_phone">{ts('conv_field_owner_phone', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
              <Input {...inputProps('owner_phone')} type="tel" required maxLength={50} dir="ltr" style={{ textAlign: 'start' }} onBlur={() => recheck(form)} autoComplete="off" />
              <FieldError id="conv-owner_phone" message={errors.owner_phone} />
              {fieldNote('owner_phone')}
            </div>
          </div>
          <div>
            <Label htmlFor="conv-address">{ts('conv_field_address', language)}<span className="text-gray-400 text-xs"> ({ts('field_optional', language)})</span></Label>
            <Input {...inputProps('address')} maxLength={500} dir="auto" />
            <FieldError id="conv-address" message={errors.address} />
          </div>

          <div>
            <Label htmlFor="conv-plan">{ts('conv_field_plan', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
            {noPlans ? (
              <p className="text-sm text-amber-700 mt-1" data-testid="conv-plan-none">{ts('conv_plan_none', language)}</p>
            ) : (
              <Select value={form.subscription_plan_id} onValueChange={(v) => set('subscription_plan_id', v)}>
                <SelectTrigger id="conv-plan" className="mt-1" data-testid="conv-plan"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {pre.plans.map((plan) => <SelectItem key={plan.id} value={plan.id} data-testid={`conv-plan-option-${plan.id}`}>{planLabel(plan, language)}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
            <FieldError id="conv-plan" message={errors.subscription_plan_id} />
          </div>

          <div>
            <Label htmlFor="conv-owner_password">{ts('conv_field_password', language)}<span className="text-red-600" aria-hidden="true"> *</span></Label>
            <div className="flex flex-wrap gap-2 mt-1">
              <Input
                id="conv-owner_password" type={showPassword ? 'text' : 'password'} value={form.owner_password} required minLength={6} maxLength={200}
                onChange={(e) => set('owner_password', e.target.value)} dir="ltr" style={{ textAlign: 'start' }} autoComplete="new-password"
                aria-invalid={!!errors.owner_password} className={`flex-1 min-w-[9rem] ${errors.owner_password ? 'border-red-500' : ''}`} data-testid="conv-owner_password"
              />
              <Button type="button" variant="outline" size="icon" className="rounded-sm shrink-0" onClick={() => setShowPassword((v) => !v)}
                aria-label={ts(showPassword ? 'conv_password_hide' : 'conv_password_show', language)} data-testid="conv-password-toggle">
                {showPassword ? <EyeOff className="w-4 h-4" aria-hidden="true" /> : <Eye className="w-4 h-4" aria-hidden="true" />}
              </Button>
              <Button type="button" variant="outline" className="rounded-sm shrink-0" onClick={() => { set('owner_password', generatePassword()); setShowPassword(true); }} data-testid="conv-password-generate">
                <Sparkles className="w-4 h-4 me-1.5" aria-hidden="true" />{ts('conv_password_generate', language)}
              </Button>
              <Button type="button" variant="outline" size="icon" className="rounded-sm shrink-0" disabled={!form.owner_password} onClick={copyPassword}
                aria-label={ts('conv_password_copy', language)} data-testid="conv-password-copy">
                <Copy className="w-4 h-4" aria-hidden="true" />
              </Button>
            </div>
            <p className="text-xs text-gray-500 mt-1">{ts('conv_password_hint', language)}</p>
            <FieldError id="conv-owner_password" message={errors.owner_password} />
          </div>

          {needsConfirm && !pre.duplicates.has_exact && (
            <label htmlFor="conv-confirm" className="flex items-start gap-2.5 rounded-md border border-amber-200 bg-amber-50 p-3 cursor-pointer" data-testid="conv-confirm-row">
              <Checkbox id="conv-confirm" checked={confirm} onCheckedChange={(v) => setConfirm(v === true)} className="mt-0.5" data-testid="conv-confirm" />
              <span className="text-sm text-gray-800">{ts('conv_confirm_duplicates', language)}</span>
            </label>
          )}
          {checking && <p className="text-xs text-gray-500" role="status">{ts('conv_checking', language)}</p>}
        </>
      )}
    </ActivityFormDialog>
  );
};

export default ConvertLeadDialog;
