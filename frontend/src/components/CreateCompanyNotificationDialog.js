import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { toast } from 'sonner';
import { validateAndFocus } from '@/utils/formValidation';
import { Bell, Plus, Trash2, ArrowRight, ArrowLeft } from 'lucide-react';

const RECIPIENT_MODES = [
  { value: 'all_managers', label: 'جميع المدراء' },
  { value: 'all_employees', label: 'جميع الموظفين' },
  { value: 'everyone', label: 'الجميع' },
  { value: 'custom', label: 'تحديد المستلمين' },
];

const CUSTOM_GROUP_MODES = [
  { value: 'managers', label: 'المدراء' },
  { value: 'employees', label: 'الموظفين' },
  { value: 'everyone', label: 'الجميع' },
];

const emptyForm = { recipient_mode: 'all_managers', title_ar: '', title_en: '', message_ar: '', message_en: '' };
const emptyRow = () => ({ company_id: '', mode: 'everyone' });

// Two-step flow inside one dialog: fill in targeting + bilingual content,
// then a review step showing the real resolved recipient count (from
// POST /admin/company-notifications/preview - the exact same resolver the
// actual send uses) before the irreversible broadcast fires. No fake
// recipient counts, no client-side estimate.
const CreateCompanyNotificationDialog = ({ open, onOpenChange, onCreated }) => {
  const [step, setStep] = useState('form');
  const [form, setForm] = useState(emptyForm);
  const [customRows, setCustomRows] = useState([emptyRow()]);
  const [companies, setCompanies] = useState([]);
  const [previewCount, setPreviewCount] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [sending, setSending] = useState(false);
  // One key per compose session, generated when the dialog opens for a
  // new notification and reused for every retry of that same send (a
  // double-click, a timeout-then-retry, a network-level retry) - the
  // backend uses it to guarantee the same logical request never creates
  // two broadcasts. A fresh key only appears the next time the dialog is
  // opened fresh (see close()), so two genuinely separate notifications
  // are never accidentally treated as duplicates of each other.
  const [idempotencyKey, setIdempotencyKey] = useState(null);

  useEffect(() => {
    if (open && companies.length === 0) {
      api.get('/admin/companies').then((res) => setCompanies(res.data)).catch(() => {});
    }
    if (open && !idempotencyKey) {
      setIdempotencyKey(crypto.randomUUID());
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    onOpenChange(false);
    setStep('form');
    setForm(emptyForm);
    setCustomRows([emptyRow()]);
    setPreviewCount(null);
    setIdempotencyKey(null);
  };

  const buildTargetConfig = () => {
    if (form.recipient_mode !== 'custom') return null;
    const valid = customRows.filter((r) => r.company_id && r.mode);
    return { companies: valid.map((r) => ({ company_id: r.company_id, mode: r.mode })) };
  };

  const handleReview = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    const target_config = buildTargetConfig();
    if (form.recipient_mode === 'custom' && !target_config.companies.length) {
      toast.error('أضف شركة واحدة على الأقل لتحديد المستلمين');
      return;
    }
    setPreviewing(true);
    try {
      const res = await api.post('/admin/company-notifications/preview', {
        recipient_mode: form.recipient_mode,
        target_config,
      });
      setPreviewCount(res.data.recipient_count);
      setStep('confirm');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ أثناء حساب عدد المستلمين');
    }
    setPreviewing(false);
  };

  const handleSend = async () => {
    setSending(true);
    try {
      await api.post('/admin/company-notifications', {
        recipient_mode: form.recipient_mode,
        target_config: buildTargetConfig(),
        title_ar: form.title_ar,
        title_en: form.title_en,
        message_ar: form.message_ar,
        message_en: form.message_en,
        idempotency_key: idempotencyKey,
      });
      toast.success('تم إرسال الإشعار بنجاح');
      onCreated?.();
      close();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ أثناء الإرسال');
    }
    setSending(false);
  };

  const updateRow = (index, patch) => {
    setCustomRows((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); }}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" data-testid="create-company-notification-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bell className="w-5 h-5 text-[#0033A0]" /> إشعار جديد للشركات
          </DialogTitle>
          <DialogDescription>
            {step === 'form'
              ? 'اختر المستلمين وأدخل محتوى الإشعار باللغتين العربية والإنجليزية.'
              : 'راجع الإشعار قبل الإرسال - لا يمكن التراجع بعد الإرسال.'}
          </DialogDescription>
        </DialogHeader>

        {step === 'form' ? (
          <form onSubmit={handleReview} className="space-y-4">
            <div>
              <Label>المستلمون</Label>
              <Select value={form.recipient_mode} onValueChange={(v) => setForm({ ...form, recipient_mode: v })}>
                <SelectTrigger data-testid="recipient-mode-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {RECIPIENT_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {form.recipient_mode === 'custom' && (
              <div className="space-y-2 border border-gray-200 rounded-md p-3">
                {customRows.map((row, i) => (
                  <div key={i} className="flex items-center gap-2" data-testid={`custom-row-${i}`}>
                    <Select value={row.company_id} onValueChange={(v) => updateRow(i, { company_id: v })}>
                      <SelectTrigger className="flex-1" data-testid={`custom-row-${i}-company`}>
                        <SelectValue placeholder="اختر الشركة..." />
                      </SelectTrigger>
                      <SelectContent>
                        {companies.map((c) => (
                          <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select value={row.mode} onValueChange={(v) => updateRow(i, { mode: v })}>
                      <SelectTrigger className="w-36" data-testid={`custom-row-${i}-mode`}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {CUSTOM_GROUP_MODES.map((m) => (
                          <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      type="button" variant="ghost" size="sm"
                      onClick={() => setCustomRows((rows) => rows.filter((_, idx) => idx !== i))}
                      disabled={customRows.length === 1}
                      data-testid={`custom-row-${i}-remove`}
                    >
                      <Trash2 className="w-4 h-4 text-gray-400" />
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" onClick={() => setCustomRows((rows) => [...rows, emptyRow()])} data-testid="add-custom-row-btn">
                  <Plus className="w-4 h-4 me-1.5" /> إضافة شركة
                </Button>
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <Label>العنوان (عربي)</Label>
                <Input dir="rtl" required data-testid="title-ar-input" value={form.title_ar} onChange={(e) => setForm({ ...form, title_ar: e.target.value })} />
              </div>
              <div>
                <Label>Title (English)</Label>
                <Input dir="ltr" required data-testid="title-en-input" value={form.title_en} onChange={(e) => setForm({ ...form, title_en: e.target.value })} />
              </div>
              <div>
                <Label>الرسالة (عربي)</Label>
                <Textarea dir="rtl" required rows={3} data-testid="message-ar-input" value={form.message_ar} onChange={(e) => setForm({ ...form, message_ar: e.target.value })} />
              </div>
              <div>
                <Label>Message (English)</Label>
                <Textarea dir="ltr" required rows={3} data-testid="message-en-input" value={form.message_en} onChange={(e) => setForm({ ...form, message_en: e.target.value })} />
              </div>
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={close} data-testid="cancel-notification-btn">إلغاء</Button>
              <Button type="submit" disabled={previewing} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="review-notification-btn">
                {previewing ? 'جارِ الحساب...' : <>التالي <ArrowLeft className="w-4 h-4 ms-1.5" /></>}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="bg-blue-50 border border-blue-200 rounded-md p-4 text-center">
              <p className="text-sm text-gray-600">سيتم إرسال هذا الإشعار إلى</p>
              <p className="text-3xl font-bold text-[#0033A0]" data-testid="recipient-count">{previewCount?.toLocaleString('ar-EG')}</p>
              <p className="text-sm text-gray-600">مستخدماً</p>
            </div>
            <div className="space-y-2 text-sm">
              <div className="border border-gray-200 rounded-md p-3">
                <p className="text-xs text-gray-400 uppercase tracking-wider mb-1">عربي</p>
                <p className="font-bold">{form.title_ar}</p>
                <p className="text-gray-700 whitespace-pre-wrap mt-1">{form.message_ar}</p>
              </div>
              <div className="border border-gray-200 rounded-md p-3" dir="ltr">
                <p className="text-xs text-gray-400 uppercase tracking-wider mb-1">English</p>
                <p className="font-bold">{form.title_en}</p>
                <p className="text-gray-700 whitespace-pre-wrap mt-1">{form.message_en}</p>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setStep('form')} disabled={sending} data-testid="back-to-form-btn">
                <ArrowRight className="w-4 h-4 me-1.5" /> رجوع
              </Button>
              <Button type="button" onClick={handleSend} disabled={sending} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="send-notification-btn">
                {sending ? 'جارِ الإرسال...' : 'إرسال الإشعار'}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default CreateCompanyNotificationDialog;
