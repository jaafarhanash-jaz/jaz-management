import { useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import api from '@/utils/api';
import { toast } from 'sonner';
import { validateAndFocus } from '@/utils/formValidation';

const emptyForm = {
  assigned_to: [],
  title: '',
  description: '',
  execution_date: '',
  execution_time: '',
  due_date: '',
  due_time: '',
  requires_proof: false
};

const CreateUrgentTaskDialog = ({ open, onOpenChange, employees, onCreated }) => {
  const [form, setForm] = useState(emptyForm);
  const [scheduleExecution, setScheduleExecution] = useState(false);
  const [setDueDateEnabled, setSetDueDateEnabled] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // See CreateTaskDialog.js for why both of these exist: `submitting`
  // (React state) disables the button, but only after a re-render, which
  // is too slow to stop a fast double-click/double-Enter from firing
  // handleSubmit twice - submittingRef is checked synchronously instead.
  // idempotencyKeyRef is one key per dialog-open, reused across retries of
  // the same logical submission so the backend can dedupe them.
  const submittingRef = useRef(false);
  const idempotencyKeyRef = useRef(null);

  useEffect(() => {
    if (open) idempotencyKeyRef.current = crypto.randomUUID();
  }, [open]);

  const toggleEmployee = (id) => {
    setForm((f) => ({
      ...f,
      assigned_to: f.assigned_to.includes(id) ? f.assigned_to.filter((e) => e !== id) : [...f.assigned_to, id]
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    if (form.assigned_to.length === 0) {
      toast.error('يرجى اختيار موظف واحد على الأقل');
      return;
    }
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      // Toggles gate whether these fields are sent at all - disabled means
      // null (Immediate task / no deadline), regardless of any stale value
      // left over in the hidden inputs.
      await api.post('/owner/urgent-tasks', {
        ...form,
        execution_date: scheduleExecution ? (form.execution_date || null) : null,
        execution_time: scheduleExecution ? (form.execution_time || null) : null,
        due_date: setDueDateEnabled ? (form.due_date || null) : null,
        due_time: setDueDateEnabled ? (form.due_time || null) : null,
        idempotency_key: idempotencyKeyRef.current,
      });
      toast.success('تم إرسال المهمة بنجاح');
      setForm(emptyForm);
      setScheduleExecution(false);
      setSetDueDateEnabled(false);
      onOpenChange(false);
      if (onCreated) onCreated();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
    submittingRef.current = false;
    setSubmitting(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="urgent-task-dialog">
        <DialogHeader><DialogTitle>مهمة جديدة</DialogTitle></DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <Label>الموظفون</Label>
            <div className="mt-1 border border-gray-200 rounded-sm p-3 max-h-32 overflow-y-auto space-y-2">
              {employees.map((emp) => (
                <div key={emp.id} className="flex items-center gap-2">
                  <Checkbox
                    id={`urgent-emp-${emp.id}`}
                    data-testid={`urgent-emp-${emp.id}`}
                    checked={form.assigned_to.includes(emp.id)}
                    onCheckedChange={() => toggleEmployee(emp.id)}
                  />
                  <Label htmlFor={`urgent-emp-${emp.id}`} className="cursor-pointer font-normal">{emp.name}</Label>
                </div>
              ))}
              {employees.length === 0 && <p className="text-xs text-gray-500">لا يوجد موظفون</p>}
            </div>
          </div>
          <div><Label>العنوان</Label><Input data-testid="urgent-title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div><Label>الوصف</Label><Textarea data-testid="urgent-description" required rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <div>
            <div className="flex items-center gap-2">
              <Checkbox id="urgent-schedule-toggle" data-testid="urgent-schedule-toggle" checked={scheduleExecution} onCheckedChange={(v) => setScheduleExecution(!!v)} />
              <Label htmlFor="urgent-schedule-toggle" className="cursor-pointer">جدولة التنفيذ (اتركه معطلاً لمهمة فورية)</Label>
            </div>
            {scheduleExecution && (
              <div className="grid grid-cols-2 gap-3 mt-2">
                <div><Label>تاريخ التنفيذ</Label><Input data-testid="urgent-execution-date" type="date" value={form.execution_date} onChange={(e) => setForm({ ...form, execution_date: e.target.value })} /></div>
                <div><Label>وقت التنفيذ</Label><Input data-testid="urgent-execution-time" type="time" value={form.execution_time} onChange={(e) => setForm({ ...form, execution_time: e.target.value })} /></div>
              </div>
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <Checkbox id="urgent-due-toggle" data-testid="urgent-due-toggle" checked={setDueDateEnabled} onCheckedChange={(v) => setSetDueDateEnabled(!!v)} />
              <Label htmlFor="urgent-due-toggle" className="cursor-pointer">تحديد تاريخ استحقاق</Label>
            </div>
            {setDueDateEnabled && (
              <div className="grid grid-cols-2 gap-3 mt-2">
                <div><Label>تاريخ الاستحقاق</Label><Input data-testid="urgent-due-date" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></div>
                <div><Label>وقت الاستحقاق</Label><Input data-testid="urgent-due-time" type="time" value={form.due_time} onChange={(e) => setForm({ ...form, due_time: e.target.value })} /></div>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id="urgent-proof" data-testid="urgent-proof" checked={form.requires_proof} onCheckedChange={(v) => setForm({ ...form, requires_proof: !!v })} />
            <Label htmlFor="urgent-proof" className="cursor-pointer">يتطلب إثباتاً بالصورة</Label>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>إلغاء</Button>
            <Button type="submit" disabled={submitting} className="bg-[#0033A0] hover:bg-[#002277] text-white" data-testid="send-urgent-task-btn">
              {submitting ? 'جاري الإرسال...' : 'إرسال المهمة'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default CreateUrgentTaskDialog;
