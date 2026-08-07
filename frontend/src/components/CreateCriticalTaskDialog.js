import { useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { toast } from 'sonner';
import { Siren, Paperclip, X, AlertTriangle } from 'lucide-react';

const emptyForm = {
  assigned_to: '',
  title: '',
  description: '',
  due_date: '',
  due_time: '',
  requires_proof: false
};

// Calls the existing, unmodified POST /owner/tasks with priority: "critical"
// - Task Management (Owner/Tasks.js) is off-limits, so this dialog is the
// dedicated entry point for the Urgent Task workflow instead. "Urgent Task"
// here is the user-facing label; internally still priority="critical".
const CreateCriticalTaskDialog = ({ open, onOpenChange, employees, onCreated }) => {
  const [form, setForm] = useState(emptyForm);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [step, setStep] = useState('form'); // 'form' | 'confirm'
  const [submitting, setSubmitting] = useState(false);
  // See CreateTaskDialog.js: submittingRef is a synchronous guard against a
  // fast double-click on "إرسال المهمة العاجلة الآن" firing
  // handleConfirmedSend twice before React disables the button; the
  // idempotency key is one per dialog-open, reused across retries of the
  // same logical submission.
  const submittingRef = useRef(false);
  const idempotencyKeyRef = useRef(null);

  useEffect(() => {
    if (open) idempotencyKeyRef.current = crypto.randomUUID();
  }, [open]);

  const reset = () => {
    setForm(emptyForm);
    setPendingFiles([]);
    setStep('form');
  };

  const close = () => {
    onOpenChange(false);
    reset();
  };

  const addPendingFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setPendingFiles((prev) => [...prev, { filename: file.name, mime_type: file.type || 'application/octet-stream', data: reader.result }]);
    };
    reader.readAsDataURL(file);
    e.target.value = '';
  };

  const goToConfirm = (e) => {
    e.preventDefault();
    if (!form.assigned_to) {
      toast.error('يرجى اختيار الموظف');
      return;
    }
    if (!form.due_date) {
      toast.error('يرجى تحديد تاريخ الاستحقاق');
      return;
    }
    setStep('confirm');
  };

  const selectedEmployee = employees.find((e) => e.id === form.assigned_to);

  const handleConfirmedSend = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      await api.post('/owner/tasks', {
        ...form,
        due_time: form.due_time || null,
        priority: 'critical',
        attachments: pendingFiles,
        idempotency_key: idempotencyKeyRef.current,
      });
      toast.success('تم إرسال المهمة العاجلة بنجاح');
      close();
      if (onCreated) onCreated();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
    submittingRef.current = false;
    setSubmitting(false);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); else onOpenChange(v); }}>
      <DialogContent className="max-w-lg" data-testid="critical-task-dialog">
        {step === 'form' ? (
          <>
            <DialogHeader><DialogTitle className="flex items-center gap-2"><Siren className="w-5 h-5 text-red-700" /> مهمة عاجلة جديدة</DialogTitle></DialogHeader>
            <p className="text-sm text-gray-500 -mt-2">سيظهر تنبيه بملء الشاشة مع صوت تنبيه متكرر للموظف المعني حتى يستلم المهمة أو يبدأ بها.</p>
            <form onSubmit={goToConfirm} className="space-y-4 max-h-[65vh] overflow-y-auto">
              <div>
                <Label>الموظف</Label>
                <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                  <SelectTrigger data-testid="critical-task-assignee-select"><SelectValue placeholder="اختر الموظف" /></SelectTrigger>
                  <SelectContent>
                    {employees.map((emp) => (<SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>))}
                  </SelectContent>
                </Select>
              </div>
              <div><Label>العنوان</Label><Input data-testid="critical-task-title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
              <div><Label>الوصف التفصيلي</Label><Textarea data-testid="critical-task-description" required rows={4} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>تاريخ الاستحقاق</Label><Input data-testid="critical-task-due-date" type="date" required value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></div>
                <div><Label>وقت الاستحقاق (اختياري)</Label><Input data-testid="critical-task-due-time" type="time" value={form.due_time} onChange={(e) => setForm({ ...form, due_time: e.target.value })} /></div>
              </div>
              <div>
                <Label>مرفقات (اختياري)</Label>
                <input type="file" onChange={addPendingFile} className="text-sm block mt-1" data-testid="critical-task-file-input" />
                {pendingFiles.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {pendingFiles.map((f, i) => (
                      <li key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-2 py-1">
                        <span><Paperclip className="w-3 h-3 inline me-1" /> {f.filename}</span>
                        <button type="button" onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))} data-testid={`critical-task-remove-file-${i}`}>
                          <X className="w-3 h-3" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Checkbox id="critical-task-proof" data-testid="critical-task-proof" checked={form.requires_proof} onCheckedChange={(v) => setForm({ ...form, requires_proof: !!v })} />
                <Label htmlFor="critical-task-proof" className="cursor-pointer">يتطلب إثباتاً بالصورة</Label>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={close}>إلغاء</Button>
                <Button type="submit" className="bg-red-700 hover:bg-red-800 text-white" data-testid="critical-task-next-btn">
                  التالي
                </Button>
              </DialogFooter>
            </form>
          </>
        ) : (
          <>
            <DialogHeader><DialogTitle className="flex items-center gap-2 text-red-800"><AlertTriangle className="w-5 h-5" /> تأكيد إرسال المهمة العاجلة</DialogTitle></DialogHeader>
            <div className="bg-red-50 border border-red-200 rounded-md p-3 text-sm text-red-800" data-testid="critical-task-warning">
              سيؤدي إرسال هذه المهمة إلى مقاطعة الموظف <b>{selectedEmployee?.name}</b> فوراً بتنبيه بملء الشاشة لا يمكن تجاهله أو إغلاقه، ولن يتوقف الصوت إلا عند ضغط "استلام المهمة" أو "بدء العمل الآن".
            </div>
            <div className="border border-gray-200 rounded-md p-4 space-y-2 text-sm" data-testid="critical-task-summary">
              <div className="flex justify-between"><span className="text-gray-500">الموظف</span><span className="font-medium">{selectedEmployee?.name || '-'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">العنوان</span><span className="font-medium">{form.title}</span></div>
              <div className="text-gray-600 whitespace-pre-wrap">{form.description}</div>
              <div className="flex justify-between"><span className="text-gray-500">الاستحقاق</span><span className="font-medium">{form.due_date}{form.due_time ? ` - ${form.due_time}` : ''}</span></div>
              {pendingFiles.length > 0 && (
                <div className="flex justify-between"><span className="text-gray-500">المرفقات</span><span className="font-medium">{pendingFiles.length}</span></div>
              )}
              {form.requires_proof && <div className="text-xs text-gray-500">يتطلب إثباتاً بالصورة عند الإنجاز</div>}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setStep('form')} data-testid="critical-task-back-btn">رجوع</Button>
              <Button
                type="button"
                onClick={handleConfirmedSend}
                disabled={submitting}
                className="bg-red-700 hover:bg-red-800 text-white"
                data-testid="send-critical-task-btn"
              >
                {submitting ? 'جاري الإرسال...' : 'إرسال المهمة العاجلة الآن'}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default CreateCriticalTaskDialog;
