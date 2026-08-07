import { useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import api from '@/utils/api';
import { toast } from 'sonner';
import { CalendarClock, GitBranch, ArrowUp, ArrowDown, X as XIcon, Paperclip } from 'lucide-react';

const emptyForm = () => ({
  title: '', description: '', priority: 'medium',
  assigned_to: '', due_date: '', due_time: '', requires_proof: false,
});

// The single canonical "create/edit task" dialog - identical logic and
// endpoints (POST/PUT /owner/tasks, POST /owner/tasks/workflow) as the
// original inline dialog in Owner/Tasks.js, extracted so Calendar.js can
// reuse it verbatim instead of a second implementation.
const CreateTaskDialog = ({ open, onOpenChange, employees, onCreated, editingTask = null, defaultDueDate = null }) => {
  const editingId = editingTask?.id || null;

  const [form, setForm] = useState(emptyForm());
  const [pendingFiles, setPendingFiles] = useState([]);
  // Scheduled Task (Part 3) - optional future activation, disabled by
  // default so a plain task's behavior is exactly as before.
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleDate, setScheduleDate] = useState('');
  const [scheduleTime, setScheduleTime] = useState('');
  // Sequential Workflow (Part 4) - optional ordered multi-employee chain,
  // disabled by default; when off the dialog behaves exactly as before
  // (single employee via form.assigned_to).
  const [sequentialEnabled, setSequentialEnabled] = useState(false);
  const [employeeOrder, setEmployeeOrder] = useState([]);
  const [saving, setSaving] = useState(false);
  // Belt-and-suspenders against double submission: `saving` only disables
  // the submit button after React re-renders, which is asynchronous - a
  // fast double-click/double-Enter can fire handleSubmit twice before that
  // happens (confirmed in production: two identical tasks created 0.0s
  // apart). This ref is checked synchronously, no render in between.
  const submittingRef = useRef(false);
  // One idempotency key per "dialog session" (regenerated whenever it's
  // opened for a fresh task), not per handleSubmit call - a retried/raced
  // request must resend the *same* key for the backend's dedup to work.
  const idempotencyKeyRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    idempotencyKeyRef.current = crypto.randomUUID();
    if (editingTask) {
      setForm({
        title: editingTask.title || '', description: editingTask.description || '',
        priority: editingTask.priority || 'medium', assigned_to: editingTask.assigned_to || '',
        due_date: editingTask.due_date || '', due_time: '', requires_proof: editingTask.requires_proof || false,
      });
    } else {
      setForm({ ...emptyForm(), due_date: defaultDueDate || '' });
    }
    setPendingFiles([]);
    setScheduleEnabled(false);
    setScheduleDate('');
    setScheduleTime('');
    setSequentialEnabled(false);
    setEmployeeOrder([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editingTask, defaultDueDate]);

  const close = () => onOpenChange(false);

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

  const addToWorkflow = (employeeId) => {
    if (!employeeId || employeeOrder.includes(employeeId)) return;
    setEmployeeOrder((prev) => [...prev, employeeId]);
  };
  const removeFromWorkflow = (employeeId) => setEmployeeOrder((prev) => prev.filter((id) => id !== employeeId));
  const moveInWorkflow = (index, direction) => {
    setEmployeeOrder((prev) => {
      const next = [...prev];
      const swapWith = index + direction;
      if (swapWith < 0 || swapWith >= next.length) return prev;
      [next[index], next[swapWith]] = [next[swapWith], next[index]];
      return next;
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSaving(true);
    try {
      if (editingId) {
        await api.put(`/owner/tasks/${editingId}`, {
          title: form.title, description: form.description, priority: form.priority,
          assigned_to: form.assigned_to, due_date: form.due_date, requires_proof: form.requires_proof,
        });
        toast.success('تم التحديث بنجاح');
      } else if (sequentialEnabled) {
        if (employeeOrder.length < 2) {
          toast.error('سير العمل التسلسلي يتطلب موظفَين على الأقل بالترتيب');
          submittingRef.current = false;
          setSaving(false);
          return;
        }
        await api.post('/owner/tasks/workflow', {
          title: form.title, description: form.description, priority: form.priority,
          employee_order: employeeOrder, due_date: form.due_date || null, due_time: form.due_time || null,
          requires_proof: form.requires_proof, attachments: pendingFiles,
          scheduled_date: scheduleEnabled ? scheduleDate : null, scheduled_time: scheduleEnabled ? scheduleTime : null,
          idempotency_key: idempotencyKeyRef.current,
        });
        toast.success('تم إنشاء سير العمل التسلسلي');
      } else {
        await api.post('/owner/tasks', {
          ...form,
          due_time: form.due_time || null,
          attachments: pendingFiles,
          scheduled_date: scheduleEnabled ? scheduleDate : null,
          scheduled_time: scheduleEnabled ? scheduleTime : null,
          idempotency_key: idempotencyKeyRef.current,
        });
        toast.success(scheduleEnabled ? 'تمت جدولة المهمة' : 'تمت الإضافة بنجاح');
      }
      close();
      if (onCreated) onCreated();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
    submittingRef.current = false;
    setSaving(false);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); else onOpenChange(v); }}>
      <DialogContent className="max-w-lg" data-testid="task-dialog">
        <DialogHeader>
          <DialogTitle>{editingId ? 'تعديل المهمة' : 'إضافة مهمة جديدة'}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <Label>العنوان</Label>
            <Input data-testid="task-title-input" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <Label>الوصف</Label>
            <Textarea data-testid="task-description-input" required value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>الأولوية</Label>
              <Select value={form.priority} onValueChange={(v) => setForm({ ...form, priority: v })}>
                <SelectTrigger data-testid="task-priority-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="critical">عاجلة</SelectItem>
                  <SelectItem value="high">عالية</SelectItem>
                  <SelectItem value="medium">متوسطة</SelectItem>
                  <SelectItem value="low">منخفضة</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>موعد التسليم</Label>
              <Input data-testid="task-due-date-input" type="date" required value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
            </div>
          </div>
          {!editingId && (
            <div>
              <Label>وقت التسليم (اختياري)</Label>
              <Input data-testid="task-due-time-input" type="time" value={form.due_time} onChange={(e) => setForm({ ...form, due_time: e.target.value })} />
            </div>
          )}
          {!sequentialEnabled && (
            <div>
              <Label>الموظف المسؤول</Label>
              <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                <SelectTrigger data-testid="task-assignee-select"><SelectValue placeholder="اختر الموظف" /></SelectTrigger>
                <SelectContent>
                  {employees.map(emp => (<SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Checkbox id="requires_proof" data-testid="task-proof-checkbox" checked={form.requires_proof} onCheckedChange={(v) => setForm({ ...form, requires_proof: !!v })} />
            <Label htmlFor="requires_proof" className="cursor-pointer">يتطلب إثباتاً (صورة/ملف)</Label>
          </div>

          {!editingId && (
            <div className="border-t border-gray-100 pt-4 space-y-4">
              <div>
                <Label>مرفقات (اختياري)</Label>
                <input type="file" onChange={addPendingFile} className="text-sm block mt-1" data-testid="task-file-input" />
                {pendingFiles.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {pendingFiles.map((f, i) => (
                      <li key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-2 py-1">
                        <span><Paperclip className="w-3 h-3 inline me-1" /> {f.filename}</span>
                        <button type="button" onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))} data-testid={`task-remove-file-${i}`}>
                          <XIcon className="w-3 h-3" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Scheduled Task (Part 3) */}
              <div>
                <div className="flex items-center gap-2">
                  <Checkbox id="schedule_enabled" data-testid="task-schedule-checkbox" checked={scheduleEnabled} onCheckedChange={(v) => setScheduleEnabled(!!v)} />
                  <Label htmlFor="schedule_enabled" className="cursor-pointer flex items-center gap-1.5">
                    <CalendarClock className="w-3.5 h-3.5 text-gray-500" /> جدولة التفعيل لوقت لاحق
                  </Label>
                </div>
                {scheduleEnabled && (
                  <div className="grid grid-cols-2 gap-4 mt-3 ps-6 animate-in fade-in slide-in-from-top-1 duration-200">
                    <div>
                      <Label className="text-xs">تاريخ التفعيل</Label>
                      <Input type="date" required={scheduleEnabled} data-testid="task-schedule-date-input" value={scheduleDate} onChange={(e) => setScheduleDate(e.target.value)} />
                    </div>
                    <div>
                      <Label className="text-xs">وقت التفعيل (اختياري)</Label>
                      <Input type="time" data-testid="task-schedule-time-input" value={scheduleTime} onChange={(e) => setScheduleTime(e.target.value)} />
                    </div>
                    <p className="col-span-2 text-[11px] text-gray-400">لن تظهر المهمة للموظف ولن تُحتسب في لوحة التحكم حتى هذا الموعد.</p>
                  </div>
                )}
              </div>

              {/* Sequential Workflow (Part 4) */}
              <div>
                <div className="flex items-center gap-2">
                  <Checkbox id="sequential_enabled" data-testid="task-sequential-checkbox" checked={sequentialEnabled} onCheckedChange={(v) => setSequentialEnabled(!!v)} />
                  <Label htmlFor="sequential_enabled" className="cursor-pointer flex items-center gap-1.5">
                    <GitBranch className="w-3.5 h-3.5 text-gray-500" /> سير عمل تسلسلي (عدة موظفين بالترتيب)
                  </Label>
                </div>
                {sequentialEnabled && (
                  <div className="mt-3 ps-6 space-y-3 animate-in fade-in slide-in-from-top-1 duration-200">
                    <Select value="" onValueChange={addToWorkflow}>
                      <SelectTrigger data-testid="workflow-add-employee-select"><SelectValue placeholder="إضافة موظف إلى السلسلة" /></SelectTrigger>
                      <SelectContent>
                        {employees.filter((e) => !employeeOrder.includes(e.id)).map((emp) => (
                          <SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {employeeOrder.length > 0 && (
                      <ol className="space-y-1.5" data-testid="workflow-order-list">
                        {employeeOrder.map((empId, index) => {
                          const emp = employees.find((e) => e.id === empId);
                          return (
                            <li key={empId} className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-sm px-3 py-2 text-sm transition-colors" data-testid={`workflow-step-${index}`}>
                              <span className="w-5 h-5 flex items-center justify-center bg-[#0033A0] text-white text-[11px] font-bold rounded-full flex-shrink-0">{index + 1}</span>
                              <span className="flex-1 text-[#0A0A0A]">{emp?.name || empId}</span>
                              <button type="button" onClick={() => moveInWorkflow(index, -1)} disabled={index === 0} className="text-gray-400 hover:text-[#0033A0] disabled:opacity-30" data-testid={`workflow-up-${index}`}>
                                <ArrowUp className="w-3.5 h-3.5" />
                              </button>
                              <button type="button" onClick={() => moveInWorkflow(index, 1)} disabled={index === employeeOrder.length - 1} className="text-gray-400 hover:text-[#0033A0] disabled:opacity-30" data-testid={`workflow-down-${index}`}>
                                <ArrowDown className="w-3.5 h-3.5" />
                              </button>
                              <button type="button" onClick={() => removeFromWorkflow(empId)} className="text-gray-400 hover:text-red-600" data-testid={`workflow-remove-${index}`}>
                                <XIcon className="w-3.5 h-3.5" />
                              </button>
                            </li>
                          );
                        })}
                      </ol>
                    )}
                    <p className="text-[11px] text-gray-400">
                      يظهر الموظف الأول فوراً؛ عند إكماله تنتقل المهمة تلقائياً إلى التالي في الترتيب، وهكذا حتى النهاية.
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={close} data-testid="cancel-task-btn">إلغاء</Button>
            <Button type="submit" disabled={saving} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-task-btn">
              {saving ? 'جارِ الحفظ...' : 'حفظ'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default CreateTaskDialog;
