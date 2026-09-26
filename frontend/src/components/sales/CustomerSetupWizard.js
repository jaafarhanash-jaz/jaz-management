import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { CompanyMatches } from '@/components/sales/ConvertLeadDialog';
import { FieldError } from '@/components/sales/ActivityFields';
import api from '@/utils/api';
import { validateAndFocus } from '@/utils/formValidation';
import { FORM_FIELDS, formToPreflight, generatePassword, planLabel, valuesToForm } from '@/utils/salesCustomers';
import { friendlyMessage, parseApiError } from '@/utils/salesErrors';
import { formatDate, formatDateTime } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { Check, Copy, Eye, EyeOff, Plus, Sparkles, Trash2, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';

// The Sales Employee's "Agreed": Lead -> Customer -> JAZ Company -> Subscription, and optionally the company's first employees
// and tasks, in ONE server request (POST /leads/{id}/setup) - nothing is created until the last step is confirmed, and then
// everything is created together or not at all. Every decision is the server's:
//   * opening the wizard asks POST /leads/{id}/setup/preflight: the values from the lead, what would block the setup (a taken
//     owner email / phone, an exact JAZ company duplicate, a lost / unassigned lead), possible duplicates, the plans, trial days;
//   * leaving the name / email / phone fields asks the preflight again;
//   * the final request re-validates everything; its errors are shown on the step and the row they belong to.
// Passwords are typed (or generated) here, sent once, and never shown again by the server.
const STEPS = ['company', 'subscription', 'employees', 'tasks', 'review'];
const STEP_OF_FIELD = { business_name: 0, address: 0, owner_name: 0, owner_email: 0, owner_phone: 0, owner_password: 0, subscription_plan_id: 1, subscription_type: 1 };
const TASK_PRIORITIES = ['low', 'medium', 'high', 'critical'];
const EMPTY = { business_name: '', owner_name: '', owner_email: '', owner_phone: '', address: '', owner_password: '', subscription_plan_id: '', subscription_type: 'trial' };

const addDays = (isoDate, days) => {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const newEmployee = () => ({ key: Math.random().toString(36).slice(2), name: '', email: '', phone: '', password: '', position: '', show: false });
const newTask = (today) => ({ key: Math.random().toString(36).slice(2), title: '', description: '', priority: 'medium', assignee: '0', due_date: addDays(today, 1) });

const Required = () => <span className="text-red-600" aria-hidden="true"> *</span>;

// Two exclusive choices shown as cards (a Radix radio group: arrow keys, one tab stop, screen-reader state).
const ChoiceCards = ({ id, value, onChange, options, label }) => (
  <RadioGroup value={value} onValueChange={onChange} aria-label={label} className="grid grid-cols-1 sm:grid-cols-2 gap-3" data-testid={id}>
    {options.map((o) => (
      <Label key={o.value} htmlFor={`${id}-${o.value}`}
        className={`flex items-start gap-3 rounded-md border p-3 cursor-pointer font-normal ${value === o.value ? 'border-[#0033A0] bg-blue-50' : 'border-gray-200 bg-white hover:bg-gray-50'} ${o.disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
        <RadioGroupItem id={`${id}-${o.value}`} value={o.value} disabled={o.disabled} className="mt-0.5" data-testid={`${id}-${o.value}`} />
        <span className="min-w-0">
          <span className="block text-sm font-medium text-[#0A0A0A]">{o.label}</span>
          {o.description && <span className="block text-xs text-gray-600 mt-0.5">{o.description}</span>}
        </span>
      </Label>
    ))}
  </RadioGroup>
);

const Stepper = ({ step, language, onGo }) => (
  <ol className="flex flex-wrap items-center gap-2" aria-label={ts('wz_step_of', language).replace('{n}', step + 1).replace('{total}', STEPS.length)} data-testid="wz-steps">
    {STEPS.map((key, i) => {
      const done = i < step;
      const current = i === step;
      return (
        <li key={key} className="flex items-center gap-2" aria-current={current ? 'step' : undefined}>
          <button type="button" disabled={!done} onClick={() => onGo(i)} data-testid={`wz-step-${key}`}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs min-h-[32px] ${current ? 'bg-[#0033A0] text-white border-[#0033A0]'
              : done ? 'bg-blue-50 text-[#0033A0] border-blue-200 hover:bg-blue-100' : 'bg-white text-gray-500 border-gray-200'}`}>
            {done ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : <span aria-hidden="true">{i + 1}</span>}
            {ts(`wz_step_${key}`, language)}
            {(key === 'employees' || key === 'tasks') && <span className="opacity-75">({ts('wz_optional', language)})</span>}
          </button>
        </li>
      );
    })}
  </ol>
);

const CustomerSetupWizard = ({ open, onOpenChange, lead, language, onDone }) => {
  const [pre, setPre] = useState(null);                       // latest preflight; null = loading, false = failed
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(EMPTY);
  const [employeesMode, setEmployeesMode] = useState('later');
  const [employees, setEmployees] = useState([]);
  const [tasksMode, setTasksMode] = useState('later');
  const [tasks, setTasks] = useState([]);
  const [confirm, setConfirm] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState({});                   // company / owner / plan field -> message
  const [rowErrors, setRowErrors] = useState({ employees: {}, tasks: {} });   // index -> {field: message}
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const bodyRef = useRef(null);
  const requestRef = useRef(0);

  const reset = () => {
    setForm(EMPTY); setEmployees([]); setTasks([]); setEmployeesMode('later'); setTasksMode('later'); setStep(0);
    setConfirm(false); setShowPassword(false); setErrors({}); setRowErrors({ employees: {}, tasks: {} }); setGeneral('');
  };

  useEffect(() => {
    if (!open) { reset(); setPre(null); return undefined; }          // no password outlives the dialog
    let cancelled = false;
    reset();
    setPre(null);
    api.post(`/sales/leads/${lead.id}/setup/preflight`, {})
      .then((res) => {
        if (cancelled) return;
        setPre(res.data);
        setForm({ ...EMPTY, ...valuesToForm(res.data.values), subscription_plan_id: res.data.plans[0]?.id || '' });
      })
      .catch((err) => { if (!cancelled) { setPre(false); setGeneral(friendlyMessage(parseApiError(err), language)); } });
    return () => { cancelled = true; };
  }, [open, lead?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }));
    setErrors((e) => (e[name] ? { ...e, [name]: undefined } : e));
  };
  const setRow = (kind, index, name, value) => {
    const update = kind === 'employees' ? setEmployees : setTasks;
    update((rows) => rows.map((row, i) => (i === index ? { ...row, [name]: value } : row)));
    setRowErrors((all) => {
      const rowErr = all[kind][index];
      if (!rowErr || !rowErr[name]) return all;
      return { ...all, [kind]: { ...all[kind], [index]: { ...rowErr, [name]: undefined } } };
    });
  };

  const recheck = async (next) => {
    const id = ++requestRef.current;
    setChecking(true);
    try {
      const res = await api.post(`/sales/leads/${lead.id}/setup/preflight`, formToPreflight(next));
      if (id !== requestRef.current) return;
      setPre(res.data);
      if (!res.data.requires_confirmation) setConfirm(false);
    } catch (err) {
      // the wizard stays usable: completing the setup validates everything again
    }
    if (id === requestRef.current) setChecking(false);
  };

  const plan = pre && pre.plans ? pre.plans.find((x) => x.id === form.subscription_plan_id) : null;
  const today = pre ? pre.today : null;
  const localToday = pre ? (pre.local_today || pre.today) : null;   // task due dates are checked against the Baghdad date
  const trialOption = pre ? pre.subscription_options.find((o) => o.type === 'trial') : null;
  const trialDays = trialOption?.days || 7;
  const trialHours = trialOption?.hours || trialDays * 24;
  // The period as the server will set it. A trial is EXACTLY 7 x 24 hours from the moment the setup completes (shown from the
  // server's clock at the preflight); a paid subscription runs in calendar dates for the plan's months.
  const periodText = (() => {
    if (!pre || !plan) return null;
    if (form.subscription_type === 'trial') {
      const start = new Date(pre.now);
      const end = new Date(start.getTime() + trialHours * 3600 * 1000);
      return ts('wz_period_trial', language).replace('{hours}', trialHours).replace('{start}', formatDateTime(start.toISOString(), language)).replace('{end}', formatDateTime(end.toISOString(), language));
    }
    const end = addDays(today, 30 * plan.duration_months);
    return ts('wz_period', language).replace('{start}', formatDate(`${today}T12:00:00Z`, language)).replace('{end}', formatDate(`${end}T12:00:00Z`, language));
  })();
  const blockersFor = (field) => (pre?.blockers || []).filter((b) => b.field === field);
  const fieldBlocked = (pre?.blockers || []).some((b) => FORM_FIELDS.includes(b.field) || b.field === 'duplicates');
  const leadBlockers = (pre?.blockers || []).filter((b) => !FORM_FIELDS.includes(b.field) && b.field !== 'duplicates');
  const needsConfirm = !!pre && pre.duplicates && pre.duplicates.has_possible && !pre.duplicates.has_exact;
  const addingEmployees = employeesMode === 'now';
  const addingTasks = addingEmployees && tasksMode === 'now';
  const employeeLimit = plan ? plan.max_employees : null;

  // ---- moving between steps ----
  const stepProblem = () => {
    if (step === 0 && fieldBlocked) return ts('conv_blockers_title', language);
    if (step === 1 && !form.subscription_plan_id) return ts('conv_plan_none', language);
    if (step === 2 && addingEmployees && employees.length === 0) return ts('wz_emp_none', language);
    if (step === 2 && addingEmployees && employeeLimit && employees.length > employeeLimit) return ts('wz_emp_limit', language).replace('{n}', employeeLimit);
    if (step === 3 && addingTasks && tasks.length === 0) return ts('wz_task_none', language);
    return '';
  };
  const next = () => {
    if (bodyRef.current && !validateAndFocus(bodyRef.current)) return;
    const problem = stepProblem();
    if (problem) { setGeneral(problem); return; }
    setGeneral('');
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };
  const back = () => { setGeneral(''); setStep((s) => Math.max(s - 1, 0)); };

  // ---- completing ----
  const localizedProblem = (problem) => {
    const key = `wz_problem_${problem.code}`;
    const text = ts(key, language);
    return text !== key ? text : problem.message;
  };

  const applyError = (err) => {
    const parsed = parseApiError(err);
    const fields = {};
    const rows = { employees: {}, tasks: {} };
    let target = null;
    const rowError = (kind, index, field, message) => {
      rows[kind][index] = { ...(rows[kind][index] || {}), [field]: message };
      target = target === null ? STEPS.indexOf(kind) : Math.min(target, STEPS.indexOf(kind));
    };
    if (parsed.code === 'validation') {
      (parsed.extra.items || []).forEach((item) => {
        const loc = (item.loc || []).filter((part) => part !== 'body');
        if ((loc[0] === 'employees' || loc[0] === 'tasks') && Number.isInteger(loc[1])) rowError(loc[0], loc[1], loc[2] || 'name', item.msg);
        else if (loc[0]) { fields[loc[0]] = item.msg; target = target === null ? (STEP_OF_FIELD[loc[0]] ?? 0) : Math.min(target, STEP_OF_FIELD[loc[0]] ?? 0); }
      });
    } else if (parsed.code === 'employees_invalid' || parsed.code === 'tasks_invalid') {
      (parsed.extra.problems || []).forEach((pr) => rowError(parsed.code === 'employees_invalid' ? 'employees' : 'tasks', pr.index, pr.field, localizedProblem(pr)));
    } else if (parsed.code === 'employees_over_plan_limit') {
      target = 2;
    } else if (parsed.code === 'tasks_need_employees') {
      target = 3;
    } else if (parsed.field && STEP_OF_FIELD[parsed.field] !== undefined) {
      const key = `conv_blocker_${parsed.code}`;
      fields[parsed.field] = ts(key, language) !== key ? ts(key, language) : friendlyMessage(parsed, language);
      target = STEP_OF_FIELD[parsed.field];
    }
    if (parsed.extra && parsed.extra.duplicates) {
      setPre((p) => ({ ...p, duplicates: parsed.extra.duplicates, requires_confirmation: parsed.code === 'duplicates_found' }));
      target = parsed.code === 'company_exists' ? 0 : 4;
    }
    setErrors(fields);
    setRowErrors(rows);
    setGeneral(parsed.code === 'duplicates_found' ? '' : friendlyMessage(parsed, language));
    if (target !== null) setStep(target);
  };

  const submit = async () => {
    setBusy(true);
    setGeneral('');
    setErrors({});
    setRowErrors({ employees: {}, tasks: {} });
    const body = {
      business_name: form.business_name.trim(), owner_name: form.owner_name.trim(), owner_email: form.owner_email.trim(),
      owner_phone: form.owner_phone.trim(), owner_password: form.owner_password, subscription_plan_id: form.subscription_plan_id,
      subscription_type: form.subscription_type, confirm_duplicates: confirm,
      employees: addingEmployees ? employees.map((e) => {
        const row = { name: e.name.trim(), email: e.email.trim(), phone: e.phone.trim(), password: e.password };
        if (e.position.trim()) row.position = e.position.trim();
        return row;
      }) : [],
      tasks: addingTasks ? tasks.map((t) => {
        const row = { title: t.title.trim(), priority: t.priority, assignee: Number(t.assignee), due_date: t.due_date };
        if (t.description.trim()) row.description = t.description.trim();
        return row;
      }) : [],
    };
    if (form.address.trim()) body.address = form.address.trim();
    try {
      const res = await api.post(`/sales/leads/${lead.id}/setup`, body);
      toast.success(ts(res.data.already_converted ? 'wz_toast_already' : 'wz_toast_done', language));
      reset();
      onOpenChange(false);
      onDone && onDone(res.data);
    } catch (err) {
      applyError(err);
    }
    setBusy(false);
  };

  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(ts('toast_password_copied', language));
    } catch (e) {
      setShowPassword(true);                          // clipboard refused: the password stays visible for a manual copy
    }
  };

  // ---- fields ----
  const text = (name, label, props = {}) => {
    const blockers = blockersFor(name);
    return (
      <div className={props.wide ? 'sm:col-span-2' : ''}>
        <Label htmlFor={`wz-${name}`}>{label}{props.required && <Required />}{props.optional && <span className="text-gray-400 text-xs"> ({ts('field_optional', language)})</span>}</Label>
        <Input id={`wz-${name}`} value={form[name]} onChange={(e) => set(name, e.target.value)} required={props.required} type={props.type || 'text'}
          maxLength={props.maxLength || 200} dir={props.ltr ? 'ltr' : 'auto'} style={props.ltr ? { textAlign: 'start' } : undefined}
          onBlur={props.recheck ? () => recheck(form) : undefined} autoComplete="off" data-testid={`wz-${name}`}
          aria-invalid={!!(errors[name] || blockers.length)} className={`mt-1 ${errors[name] || blockers.length ? 'border-red-500' : ''}`} />
        <FieldError id={`wz-${name}`} message={errors[name]} />
        {!errors[name] && blockers.map((b) => (
          <p key={b.code} className="text-xs text-red-600 mt-1" role="alert" data-testid={`wz-${name}-blocker`}>{ts(`conv_blocker_${b.code}`, language)}</p>
        ))}
      </div>
    );
  };

  const rowInput = (kind, index, name, label, props = {}) => {
    const row = (kind === 'employees' ? employees : tasks)[index];
    const message = rowErrors[kind][index] && rowErrors[kind][index][name];
    const id = `wz-${kind}-${index}-${name}`;
    return (
      <div className={props.wide ? 'sm:col-span-2' : ''}>
        <Label htmlFor={id}>{label}{props.required && <Required />}</Label>
        <Input id={id} value={row[name]} onChange={(e) => setRow(kind, index, name, e.target.value)} required={props.required} type={props.type || 'text'}
          min={props.min} maxLength={props.maxLength || 200} dir={props.ltr ? 'ltr' : 'auto'} style={props.ltr ? { textAlign: 'start' } : undefined}
          autoComplete={props.autoComplete || 'off'} data-testid={id} aria-invalid={!!message} className={`mt-1 ${message ? 'border-red-500' : ''}`} />
        <FieldError id={id} message={message} />
      </div>
    );
  };

  const [titleBefore, titleAfter] = ts('wz_title', language).split('{name}');
  const loading = open && pre === null;
  const employeeLabel = (e, i) => e.name.trim() || ts('wz_emp_n', language).replace('{n}', i + 1);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!busy) onOpenChange(o); }}>
      <DialogContent className="max-w-2xl max-h-[92vh] overflow-y-auto" data-testid="setup-wizard">
        {/* pr-8: the shared dialog's close button sits at the physical right, over the start of an Arabic title */}
        <DialogHeader className="text-start pr-8">
          <DialogTitle>{titleBefore}<bdi>{lead?.business_name}</bdi>{titleAfter}</DialogTitle>
          <DialogDescription>{ts('wz_hint', language)}</DialogDescription>
        </DialogHeader>

        {loading && <p className="text-sm text-gray-500 py-6 text-center" data-testid="wz-loading">{ts('wz_loading', language)}</p>}

        {pre === false && <p className="text-sm text-red-600" role="alert" data-testid="wz-load-error">{general}</p>}

        {pre && (
          <form noValidate onSubmit={(e) => { e.preventDefault(); if (step < STEPS.length - 1) next(); else submit(); }} className="space-y-5">
            <Stepper step={step} language={language} onGo={(i) => { setGeneral(''); setStep(i); }} />

            {leadBlockers.length > 0 && (
              <div className="rounded-md border border-red-200 bg-red-50 p-3" role="alert" data-testid="wz-lead-blockers">
                <ul className="list-disc ps-5 text-sm text-red-800">
                  {leadBlockers.map((b) => {
                    const key = ts(`wz_blocker_${b.code}`, language) !== `wz_blocker_${b.code}` ? `wz_blocker_${b.code}` : `conv_blocker_${b.code}`;
                    return <li key={b.code}>{ts(key, language)}</li>;
                  })}
                </ul>
              </div>
            )}

            <div ref={bodyRef} className="space-y-4" data-testid={`wz-body-${STEPS[step]}`}>
              {/* ---- 1. company and owner ---- */}
              {step === 0 && (
                <>
                  {pre.duplicates.companies.length > 0 && pre.duplicates.has_exact && (
                    <div className="rounded-md border border-red-200 bg-red-50 p-3" role="alert" data-testid="wz-duplicates-exact">
                      <p className="font-semibold text-[#0A0A0A]">{ts('conv_duplicates_exact_title', language)}</p>
                      <p className="text-sm text-gray-700 mt-0.5">{ts('conv_duplicates_exact_hint', language)}</p>
                      <CompanyMatches duplicates={pre.duplicates} language={language} />
                    </div>
                  )}
                  <fieldset className="space-y-3">
                    <legend className="text-sm font-semibold text-[#0A0A0A] mb-1">{ts('wz_section_company', language)}</legend>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {text('business_name', ts('conv_field_business_name', language), { required: true, recheck: true, wide: true })}
                      {text('address', ts('conv_field_address', language), { optional: true, maxLength: 500, wide: true })}
                    </div>
                  </fieldset>
                  <fieldset className="space-y-3">
                    <legend className="text-sm font-semibold text-[#0A0A0A] mb-1">{ts('wz_section_owner', language)}</legend>
                    <p className="text-xs text-gray-500 -mt-1">{ts('wz_owner_hint', language)}</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {text('owner_name', ts('conv_field_owner_name', language), { required: true, wide: true })}
                      {text('owner_email', ts('conv_field_owner_email', language), { required: true, type: 'email', ltr: true, recheck: true })}
                      {text('owner_phone', ts('conv_field_owner_phone', language), { required: true, type: 'tel', ltr: true, recheck: true, maxLength: 50 })}
                    </div>
                    <div>
                      <Label htmlFor="wz-owner_password">{ts('conv_field_password', language)}<Required /></Label>
                      <div className="flex flex-wrap gap-2 mt-1">
                        <Input id="wz-owner_password" type={showPassword ? 'text' : 'password'} value={form.owner_password} required minLength={6} maxLength={200}
                          onChange={(e) => set('owner_password', e.target.value)} dir="ltr" style={{ textAlign: 'start' }} autoComplete="new-password"
                          aria-invalid={!!errors.owner_password} className={`flex-1 min-w-[9rem] ${errors.owner_password ? 'border-red-500' : ''}`} data-testid="wz-owner_password" />
                        <Button type="button" variant="outline" size="icon" className="rounded-sm shrink-0" onClick={() => setShowPassword((v) => !v)}
                          aria-label={ts(showPassword ? 'conv_password_hide' : 'conv_password_show', language)}>
                          {showPassword ? <EyeOff className="w-4 h-4" aria-hidden="true" /> : <Eye className="w-4 h-4" aria-hidden="true" />}
                        </Button>
                        <Button type="button" variant="outline" className="rounded-sm shrink-0" onClick={() => { set('owner_password', generatePassword()); setShowPassword(true); }} data-testid="wz-password-generate">
                          <Sparkles className="w-4 h-4 me-1.5" aria-hidden="true" />{ts('conv_password_generate', language)}
                        </Button>
                        <Button type="button" variant="outline" size="icon" className="rounded-sm shrink-0" disabled={!form.owner_password} onClick={() => copy(form.owner_password)}
                          aria-label={ts('conv_password_copy', language)}>
                          <Copy className="w-4 h-4" aria-hidden="true" />
                        </Button>
                      </div>
                      <p className="text-xs text-gray-500 mt-1">{ts('conv_password_hint', language)}</p>
                      <FieldError id="wz-owner_password" message={errors.owner_password} />
                    </div>
                  </fieldset>
                  {checking && <p className="text-xs text-gray-500" role="status">{ts('conv_checking', language)}</p>}
                </>
              )}

              {/* ---- 2. subscription ---- */}
              {step === 1 && (
                <>
                  <div>
                    <p className="text-sm font-medium text-[#0A0A0A] mb-2">{ts('wz_sub_type', language)}</p>
                    <ChoiceCards id="wz-subscription" value={form.subscription_type} onChange={(v) => set('subscription_type', v)} label={ts('wz_sub_type', language)}
                      options={[
                        { value: 'trial', label: ts('wz_sub_trial', language), description: ts('wz_sub_trial_desc', language).replace('{days}', trialDays).replace('{hours}', trialHours) },
                        { value: 'paid', label: ts('wz_sub_paid', language), description: ts('wz_sub_paid_desc', language) },
                      ]} />
                  </div>
                  <div>
                    <Label htmlFor="wz-plan">{ts('conv_field_plan', language)}<Required /></Label>
                    {pre.plans.length === 0 ? (
                      <p className="text-sm text-amber-700 mt-1" data-testid="wz-plan-none">{ts('conv_plan_none', language)}</p>
                    ) : (
                      <Select value={form.subscription_plan_id} onValueChange={(v) => set('subscription_plan_id', v)}>
                        <SelectTrigger id="wz-plan" className="mt-1" data-testid="wz-plan"><SelectValue /></SelectTrigger>
                        <SelectContent>{pre.plans.map((x) => <SelectItem key={x.id} value={x.id}>{planLabel(x, language)}</SelectItem>)}</SelectContent>
                      </Select>
                    )}
                    <FieldError id="wz-plan" message={errors.subscription_plan_id} />
                  </div>
                  {periodText && (
                    <p className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700" data-testid="wz-period">{periodText}</p>
                  )}
                </>
              )}

              {/* ---- 3. employees (optional) ---- */}
              {step === 2 && (
                <>
                  <ChoiceCards id="wz-employees-mode" value={employeesMode} label={ts('wz_emp_question', language)}
                    onChange={(v) => { setEmployeesMode(v); if (v === 'now' && employees.length === 0) setEmployees([newEmployee()]); }}
                    options={[{ value: 'later', label: ts('wz_emp_choice_later', language) }, { value: 'now', label: ts('wz_emp_choice_now', language) }]} />
                  {addingEmployees && (
                    <div className="space-y-3">
                      {employeeLimit && <p className="text-xs text-gray-500">{ts('wz_emp_limit', language).replace('{n}', employeeLimit)}</p>}
                      {employees.map((e, i) => (
                        <fieldset key={e.key} className="rounded-md border border-gray-200 p-3 space-y-3" data-testid={`wz-employee-${i}`}>
                          <div className="flex items-center justify-between gap-2">
                            <legend className="text-sm font-semibold text-[#0A0A0A]">{ts('wz_emp_n', language).replace('{n}', i + 1)}</legend>
                            <Button type="button" variant="ghost" size="sm" className="rounded-sm text-red-700" data-testid={`wz-employee-${i}-remove`}
                              onClick={() => { setEmployees((rows) => rows.filter((_, j) => j !== i)); setTasks((rows) => rows.filter((t) => Number(t.assignee) !== i).map((t) => ({ ...t, assignee: String(Number(t.assignee) > i ? Number(t.assignee) - 1 : t.assignee) }))); setRowErrors((all) => ({ ...all, employees: {} })); }}>
                              <Trash2 className="w-4 h-4 me-1" aria-hidden="true" />{ts('wz_emp_remove', language)}
                            </Button>
                          </div>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            {rowInput('employees', i, 'name', ts('wz_emp_name', language), { required: true })}
                            {rowInput('employees', i, 'position', ts('wz_emp_position', language))}
                            {rowInput('employees', i, 'email', ts('wz_emp_email', language), { required: true, type: 'email', ltr: true })}
                            {rowInput('employees', i, 'phone', ts('wz_emp_phone', language), { required: true, type: 'tel', ltr: true, maxLength: 50 })}
                          </div>
                          <div>
                            <Label htmlFor={`wz-employees-${i}-password`}>{ts('wz_emp_password', language)}<Required /></Label>
                            <div className="flex flex-wrap gap-2 mt-1">
                              <Input id={`wz-employees-${i}-password`} type={e.show ? 'text' : 'password'} value={e.password} required minLength={6} maxLength={200}
                                onChange={(ev) => setRow('employees', i, 'password', ev.target.value)} dir="ltr" style={{ textAlign: 'start' }} autoComplete="new-password"
                                aria-invalid={!!(rowErrors.employees[i] && rowErrors.employees[i].password)} data-testid={`wz-employees-${i}-password`}
                                className={`flex-1 min-w-[9rem] ${rowErrors.employees[i] && rowErrors.employees[i].password ? 'border-red-500' : ''}`} />
                              <Button type="button" variant="outline" className="rounded-sm shrink-0" onClick={() => { setRow('employees', i, 'password', generatePassword()); setRow('employees', i, 'show', true); }}>
                                <Sparkles className="w-4 h-4 me-1.5" aria-hidden="true" />{ts('conv_password_generate', language)}
                              </Button>
                              <Button type="button" variant="outline" size="icon" className="rounded-sm shrink-0" disabled={!e.password} onClick={() => copy(e.password)} aria-label={ts('conv_password_copy', language)}>
                                <Copy className="w-4 h-4" aria-hidden="true" />
                              </Button>
                            </div>
                            <FieldError id={`wz-employees-${i}-password`} message={rowErrors.employees[i] && rowErrors.employees[i].password} />
                          </div>
                        </fieldset>
                      ))}
                      <Button type="button" variant="outline" className="rounded-sm" onClick={() => setEmployees((rows) => [...rows, newEmployee()])}
                        disabled={!!employeeLimit && employees.length >= employeeLimit} data-testid="wz-employee-add">
                        <Plus className="w-4 h-4 me-1.5" aria-hidden="true" />{ts('wz_emp_add', language)}
                      </Button>
                    </div>
                  )}
                </>
              )}

              {/* ---- 4. tasks (optional) ---- */}
              {step === 3 && (
                <>
                  <ChoiceCards id="wz-tasks-mode" value={addingEmployees ? tasksMode : 'later'} label={ts('wz_task_question', language)}
                    onChange={(v) => { setTasksMode(v); if (v === 'now' && tasks.length === 0) setTasks([newTask(localToday)]); }}
                    options={[
                      { value: 'later', label: ts('wz_task_choice_later', language) },
                      { value: 'now', label: ts('wz_task_choice_now', language), disabled: !addingEmployees || employees.length === 0 },
                    ]} />
                  {!addingEmployees && <p className="text-sm text-gray-600" data-testid="wz-tasks-need-employees">{ts('wz_task_needs_employees', language)}</p>}
                  {addingTasks && (
                    <div className="space-y-3">
                      {tasks.map((t, i) => {
                        const err = rowErrors.tasks[i] || {};
                        return (
                          <fieldset key={t.key} className="rounded-md border border-gray-200 p-3 space-y-3" data-testid={`wz-task-${i}`}>
                            <div className="flex items-center justify-between gap-2">
                              <legend className="text-sm font-semibold text-[#0A0A0A]">{ts('wz_task_n', language).replace('{n}', i + 1)}</legend>
                              <Button type="button" variant="ghost" size="sm" className="rounded-sm text-red-700" onClick={() => { setTasks((rows) => rows.filter((_, j) => j !== i)); setRowErrors((all) => ({ ...all, tasks: {} })); }}>
                                <Trash2 className="w-4 h-4 me-1" aria-hidden="true" />{ts('wz_emp_remove', language)}
                              </Button>
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                              {rowInput('tasks', i, 'title', ts('wz_task_title', language), { required: true, wide: true })}
                              <div>
                                <Label htmlFor={`wz-tasks-${i}-assignee`}>{ts('wz_task_assignee', language)}<Required /></Label>
                                <Select value={t.assignee} onValueChange={(v) => setRow('tasks', i, 'assignee', v)}>
                                  <SelectTrigger id={`wz-tasks-${i}-assignee`} className="mt-1" data-testid={`wz-tasks-${i}-assignee`}><SelectValue /></SelectTrigger>
                                  <SelectContent>{employees.map((e, j) => <SelectItem key={e.key} value={String(j)}>{employeeLabel(e, j)}</SelectItem>)}</SelectContent>
                                </Select>
                                <FieldError id={`wz-tasks-${i}-assignee`} message={err.assignee} />
                              </div>
                              <div>
                                <Label htmlFor={`wz-tasks-${i}-priority`}>{ts('wz_task_priority', language)}</Label>
                                <Select value={t.priority} onValueChange={(v) => setRow('tasks', i, 'priority', v)}>
                                  <SelectTrigger id={`wz-tasks-${i}-priority`} className="mt-1"><SelectValue /></SelectTrigger>
                                  <SelectContent>{TASK_PRIORITIES.map((x) => <SelectItem key={x} value={x}>{ts(`task_priority_${x}`, language)}</SelectItem>)}</SelectContent>
                                </Select>
                              </div>
                              {rowInput('tasks', i, 'due_date', ts('wz_task_due', language), { required: true, type: 'date', min: localToday, ltr: true })}
                              <div className="sm:col-span-2">
                                <Label htmlFor={`wz-tasks-${i}-description`}>{ts('wz_task_description', language)}<span className="text-gray-400 text-xs"> ({ts('field_optional', language)})</span></Label>
                                <Textarea id={`wz-tasks-${i}-description`} rows={2} maxLength={2000} className="mt-1" value={t.description} dir="auto"
                                  onChange={(e) => setRow('tasks', i, 'description', e.target.value)} />
                              </div>
                            </div>
                          </fieldset>
                        );
                      })}
                      <Button type="button" variant="outline" className="rounded-sm" onClick={() => setTasks((rows) => [...rows, newTask(localToday)])} data-testid="wz-task-add">
                        <Plus className="w-4 h-4 me-1.5" aria-hidden="true" />{ts('wz_task_add', language)}
                      </Button>
                    </div>
                  )}
                </>
              )}

              {/* ---- 5. review ---- */}
              {step === 4 && (
                <div className="space-y-4" data-testid="wz-review">
                  <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
                    <div><dt className="text-xs text-gray-500">{ts('wz_review_company', language)}</dt><dd className="font-medium" dir="auto">{form.business_name}</dd>
                      {form.address && <dd className="text-gray-600" dir="auto">{form.address}</dd>}</div>
                    <div><dt className="text-xs text-gray-500">{ts('wz_review_owner', language)}</dt><dd className="font-medium" dir="auto">{form.owner_name}</dd>
                      <dd className="text-gray-600"><bdi dir="ltr">{form.owner_email}</bdi> · <bdi dir="ltr">{form.owner_phone}</bdi></dd></div>
                    <div><dt className="text-xs text-gray-500">{ts('wz_review_subscription', language)}</dt>
                      <dd className="font-medium">{ts(form.subscription_type === 'trial' ? 'wz_sub_trial' : 'wz_sub_paid', language)}{plan && <> · <bdi>{plan.name}</bdi></>}</dd>
                      {periodText && <dd className="text-gray-600">{periodText}</dd>}</div>
                    <div><dt className="text-xs text-gray-500">{ts('wz_review_employees', language)}</dt>
                      <dd>{addingEmployees ? employees.map((e, i) => <span key={e.key} className="block" dir="auto">{employeeLabel(e, i)}</span>) : ts('wz_review_none_employees', language)}</dd></div>
                    <div className="sm:col-span-2"><dt className="text-xs text-gray-500">{ts('wz_review_tasks', language)}</dt>
                      <dd>{addingTasks ? tasks.map((t) => <span key={t.key} className="block" dir="auto">{t.title}</span>) : ts('wz_review_none_tasks', language)}</dd></div>
                  </dl>
                  {pre.marks_won && <p className="text-sm text-gray-700 rounded-md border border-blue-200 bg-blue-50 px-3 py-2">{ts('wz_review_marks_won', language)}</p>}
                  {pre.duplicates.companies.length > 0 && !pre.duplicates.has_exact && (
                    <div className="rounded-md border border-amber-200 bg-amber-50 p-3" role="alert" data-testid="wz-duplicates-possible">
                      <div className="flex items-start gap-2.5">
                        <TriangleAlert className="w-5 h-5 mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-[#0A0A0A]">{ts('conv_duplicates_possible_title', language)}</p>
                          <p className="text-sm text-gray-700 mt-0.5">{ts('conv_duplicates_possible_hint', language)}</p>
                          <CompanyMatches duplicates={pre.duplicates} language={language} />
                        </div>
                      </div>
                    </div>
                  )}
                  {needsConfirm && (
                    <label htmlFor="wz-confirm" className="flex items-start gap-2.5 rounded-md border border-amber-200 bg-amber-50 p-3 cursor-pointer" data-testid="wz-confirm-row">
                      <Checkbox id="wz-confirm" checked={confirm} onCheckedChange={(v) => setConfirm(v === true)} className="mt-0.5" data-testid="wz-confirm" />
                      <span className="text-sm text-gray-800">{ts('conv_confirm_duplicates', language)}</span>
                    </label>
                  )}
                </div>
              )}
            </div>

            {general && <p className="text-sm text-red-600" role="alert" data-testid="wz-error">{general}</p>}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-4">
              <span className="text-xs text-gray-500">{ts('wz_step_of', language).replace('{n}', step + 1).replace('{total}', STEPS.length)}</span>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" className="rounded-sm" onClick={step === 0 ? () => onOpenChange(false) : back} disabled={busy} data-testid="wz-back">
                  {ts(step === 0 ? 'action_cancel' : 'wz_back', language)}
                </Button>
                {step < STEPS.length - 1 ? (
                  <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" disabled={busy || leadBlockers.length > 0} data-testid="wz-next">
                    {ts('wz_next', language)}
                  </Button>
                ) : (
                  <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="wz-finish"
                    disabled={busy || checking || leadBlockers.length > 0 || fieldBlocked || (needsConfirm && !confirm) || pre.already_converted || pre.plans.length === 0}>
                    {ts('wz_finish', language)}
                  </Button>
                )}
              </div>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default CustomerSetupWizard;
