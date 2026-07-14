import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, Pencil, Trash2, CheckSquare, Siren, Repeat, PauseCircle, PlayCircle, ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';
import CreateUrgentTaskDialog from '@/components/CreateUrgentTaskDialog';

const PRIORITY_COLORS = {
  critical: 'bg-red-100 text-red-800 border-red-300',
  high: 'bg-red-50 text-red-700 border-red-200',
  medium: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  low: 'bg-blue-50 text-blue-700 border-blue-200',
};

const STATUS_COLORS = {
  new: 'bg-blue-50 text-blue-700 border-blue-200',
  received: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  seen: 'bg-purple-50 text-purple-700 border-purple-200',
  in_progress: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending_review: 'bg-purple-50 text-purple-700 border-purple-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  rejected: 'bg-red-50 text-red-700 border-red-200',
  overdue: 'bg-red-50 text-red-700 border-red-200',
  cancelled: 'bg-gray-100 text-gray-500 border-gray-300',
};

const STATUS_LABELS_AR = {
  new: 'جديدة (معلقة)', received: 'تم الاستلام', seen: 'شوهدت', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة',
  completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة', cancelled: 'ملغاة'
};
const PRIORITY_LABELS_AR = { high: 'عالية', medium: 'متوسطة', low: 'منخفضة', critical: 'عاجلة' };

const getCategoryBadge = (task) => {
  if (task.task_category === 'urgent') return { label: '⚡ فورية', className: 'bg-red-50 text-red-700 border-red-200' };
  if (task.task_category === 'daily') return { label: '📋 يومية', className: 'bg-indigo-50 text-indigo-700 border-indigo-200' };
  return null;
};

const formatTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'short', timeStyle: 'short' }) : null;

const TaskTimeline = ({ task }) => {
  const steps = [
    { label: 'تم التعيين', time: task.created_at },
    { label: 'تمت المشاهدة', time: task.seen_at },
    { label: 'بدأ العمل', time: task.started_at },
    { label: 'اكتمل', time: task.completed_at }
  ];
  return (
    <div className="mt-3 pt-3 border-t border-gray-100 flex flex-wrap gap-4" data-testid={`timeline-${task.id}`}>
      {steps.map((step, i) => (
        <div key={i} className={`text-xs ${step.time ? 'text-gray-700' : 'text-gray-300'}`}>
          <p className="font-medium">{step.label}</p>
          <p>{step.time ? formatTime(step.time) : '-'}</p>
        </div>
      ))}
      {task.completed_by_name && <div className="text-xs text-gray-700"><p className="font-medium">أكملها</p><p>{task.completed_by_name}</p></div>}
    </div>
  );
};

const OwnerTasks = ({ onLogout, language, setLanguage }) => {
  const [tasks, setTasks] = useState([]);
  const [dailyTasks, setDailyTasks] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({
    title: '', description: '', priority: 'medium',
    assigned_to: '', due_date: '', requires_proof: false
  });

  const [dailyDialogOpen, setDailyDialogOpen] = useState(false);
  const [editingDailyId, setEditingDailyId] = useState(null);
  const [dailyForm, setDailyForm] = useState({ title: '', description: '', assigned_to: [], execution_time: '', requires_proof: false });

  const [urgentDialogOpen, setUrgentDialogOpen] = useState(false);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(() => fetchAll(true), 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAll = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [tasksRes, dailyRes, empRes] = await Promise.all([
        api.get('/owner/tasks'),
        api.get('/owner/daily-tasks'),
        api.get('/owner/employees')
      ]);
      setTasks(tasksRes.data);
      setDailyTasks(dailyRes.data);
      setEmployees(empRes.data);
    } catch (e) { if (!silent) toast.error('خطأ في جلب البيانات'); }
    if (!silent) setLoading(false);
  };

  const resetForm = () => {
    setForm({ title: '', description: '', priority: 'medium', assigned_to: '', due_date: '', requires_proof: false });
    setEditingId(null);
  };

  const openAdd = () => { resetForm(); setDialogOpen(true); };

  const openEdit = (task) => {
    setEditingId(task.id);
    setForm({
      title: task.title || '', description: task.description || '',
      priority: task.priority || 'medium', assigned_to: task.assigned_to || '',
      due_date: task.due_date || '', requires_proof: task.requires_proof || false
    });
    setDialogOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingId) {
        await api.put(`/owner/tasks/${editingId}`, form);
        toast.success('تم التحديث بنجاح');
      } else {
        await api.post('/owner/tasks', form);
        toast.success('تمت الإضافة بنجاح');
      }
      setDialogOpen(false);
      resetForm();
      fetchAll();
    } catch (e) { toast.error(e.response?.data?.detail || 'حدث خطأ'); }
  };

  const handleDelete = async (task) => {
    if (task.status === 'in_progress') {
      if (!window.confirm('هذه المهمة قيد التنفيذ حالياً. لا يمكن حذفها مباشرة.\n\nهل تريد إلغاءها بدلاً من ذلك؟ (سيتم الاحتفاظ بسجل المهمة)')) return;
      try {
        await api.post(`/owner/tasks/${task.id}/cancel`);
        toast.success('تم إلغاء المهمة وحفظ سجلها');
        fetchAll(true);
      } catch (e) { toast.error(e.response?.data?.detail || 'حدث خطأ'); }
      return;
    }
    if (task.status === 'completed' || task.status === 'cancelled') {
      toast.error('لا يمكن حذف مهمة مكتملة أو ملغاة - يجب الاحتفاظ بسجل المهام');
      return;
    }
    if (!window.confirm('هل أنت متأكد من حذف المهمة؟')) return;
    try {
      await api.delete(`/owner/tasks/${task.id}`);
      toast.success('تم الحذف بنجاح');
      fetchAll(true);
    } catch (e) { toast.error(e.response?.data?.detail || 'حدث خطأ'); }
  };

  // ---- Daily Tasks ----
  const resetDailyForm = () => {
    setDailyForm({ title: '', description: '', assigned_to: [], execution_time: '', requires_proof: false });
    setEditingDailyId(null);
  };

  const openAddDaily = () => { resetDailyForm(); setDailyDialogOpen(true); };

  const openEditDaily = (template) => {
    setEditingDailyId(template.id);
    setDailyForm({
      title: template.title, description: template.description,
      assigned_to: template.assigned_to || [], execution_time: template.execution_time || '',
      requires_proof: template.requires_proof || false
    });
    setDailyDialogOpen(true);
  };

  const toggleDailyEmployee = (id) => {
    setDailyForm((f) => ({
      ...f,
      assigned_to: f.assigned_to.includes(id) ? f.assigned_to.filter((e) => e !== id) : [...f.assigned_to, id]
    }));
  };

  const handleDailySubmit = async (e) => {
    e.preventDefault();
    if (dailyForm.assigned_to.length === 0) {
      toast.error('يرجى اختيار موظف واحد على الأقل');
      return;
    }
    try {
      if (editingDailyId) {
        await api.put(`/owner/daily-tasks/${editingDailyId}`, dailyForm);
        toast.success('تم تحديث المهمة اليومية');
      } else {
        await api.post('/owner/daily-tasks', dailyForm);
        toast.success('تمت إضافة المهمة اليومية');
      }
      setDailyDialogOpen(false);
      resetDailyForm();
      fetchAll(true);
    } catch (e) { toast.error(e.response?.data?.detail || 'حدث خطأ'); }
  };

  const handleToggleDaily = async (template) => {
    try {
      await api.post(`/owner/daily-tasks/${template.id}/toggle`);
      toast.success(template.is_active ? 'تم تعطيل المهمة اليومية' : 'تم تفعيل المهمة اليومية');
      fetchAll(true);
    } catch (e) { toast.error('حدث خطأ'); }
  };

  const handleDeleteDaily = async (id) => {
    if (!window.confirm('هل أنت متأكد من حذف هذه المهمة اليومية؟ لن يتم حذف السجلات السابقة.')) return;
    try {
      await api.delete(`/owner/daily-tasks/${id}`);
      toast.success('تم الحذف بنجاح');
      fetchAll(true);
    } catch (e) { toast.error('حدث خطأ'); }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center flex-wrap gap-3">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="tasks-title">
              {t('tasks', language)}
            </h1>
            <p className="text-gray-600 mt-2">إدارة المهام</p>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => setUrgentDialogOpen(true)} className="bg-red-600 hover:bg-red-700 text-white rounded-sm" data-testid="tasks-emergency-btn">
              <Siren className="w-4 h-4 me-2" /> مهمة فورية
            </Button>
            <Button data-testid="add-task-btn" onClick={openAdd} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
              <Plus className="w-4 h-4 me-2" /> إضافة مهمة
            </Button>
          </div>
        </div>

        {/* Daily Recurring Tasks Panel */}
        <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6" data-testid="daily-tasks-panel">
          <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-2">
              <Repeat className="w-5 h-5 text-indigo-600" />
              <h2 className="text-xl font-bold text-[#0A0A0A]">📋 المهام اليومية المتكررة</h2>
            </div>
            <Button size="sm" variant="outline" onClick={openAddDaily} data-testid="add-daily-task-btn">
              <Plus className="w-4 h-4 me-2" /> إضافة مهمة يومية
            </Button>
          </div>
          {dailyTasks.length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">لا توجد مهام يومية بعد</p>
          ) : (
            <div className="grid gap-3">
              {dailyTasks.map((dt) => (
                <div key={dt.id} className="p-4 border border-gray-100 rounded-sm flex justify-between items-start gap-3 flex-wrap" data-testid={`daily-task-${dt.id}`}>
                  <div className="flex-1 min-w-[200px]">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-medium text-[#0A0A0A]">{dt.title}</h3>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${dt.is_active ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-300'}`}>
                        {dt.is_active ? 'مفعّلة' : 'معطّلة'}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 mb-1">{dt.description}</p>
                    <div className="flex flex-wrap gap-3 text-xs text-gray-500">
                      <span>الموظفون: <b>{dt.assigned_to_names?.join('، ') || '-'}</b></span>
                      {dt.execution_time && <span>الوقت: <b>{dt.execution_time}</b></span>}
                      {dt.requires_proof && <span className="text-orange-600">يتطلب إثباتاً</span>}
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => handleToggleDaily(dt)} title={dt.is_active ? 'تعطيل' : 'تفعيل'} data-testid={`toggle-daily-${dt.id}`}>
                      {dt.is_active ? <PauseCircle className="w-4 h-4 text-gray-600" /> : <PlayCircle className="w-4 h-4 text-green-600" />}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => openEditDaily(dt)} data-testid={`edit-daily-${dt.id}`}>
                      <Pencil className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDeleteDaily(dt.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-daily-${dt.id}`}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : tasks.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <CheckSquare className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد مهام حالياً</p>
          </Card>
        ) : (
          <div className="grid gap-4">
            {tasks.map((task) => {
              const categoryBadge = getCategoryBadge(task);
              const isExpanded = expandedId === task.id;
              return (
              <Card key={task.id} className="p-6 bg-white border border-gray-200 rounded-md task-card" data-testid={`task-card-${task.id}`}>
                <div className="flex justify-between items-start gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      {categoryBadge && (
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${categoryBadge.className}`}>{categoryBadge.label}</span>
                      )}
                      <h3 className="text-lg font-medium text-[#0A0A0A]">{task.title}</h3>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[task.priority]}`}>
                        {PRIORITY_LABELS_AR[task.priority]}
                      </span>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[task.status]}`}>
                        {STATUS_LABELS_AR[task.status] || task.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 mb-2">{task.description}</p>
                    <div className="flex flex-wrap gap-4 text-xs text-gray-500">
                      <span>الموظف: <b>{task.assigned_to_name || '-'}</b></span>
                      <span>موعد التسليم: <b>{task.due_date}{task.due_time ? ` ${task.due_time}` : ''}</b></span>
                      {task.execution_date && <span>وقت التنفيذ: <b>{task.execution_date}{task.execution_time ? ` ${task.execution_time}` : ''}</b></span>}
                      {task.task_category === 'urgent' && !task.execution_date && <span className="text-red-600">فورية</span>}
                      {task.requires_proof && <span className="text-orange-600">يتطلب إثباتاً</span>}
                    </div>
                    {task.task_category && (
                      <button onClick={() => setExpandedId(isExpanded ? null : task.id)} className="text-xs text-[#0033A0] mt-2 flex items-center gap-1" data-testid={`timeline-toggle-${task.id}`}>
                        {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />} عرض السجل الزمني
                      </button>
                    )}
                    {isExpanded && <TaskTimeline task={task} />}
                  </div>
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(task)} data-testid={`edit-task-${task.id}`}>
                      <Pencil className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(task)} className="text-red-600 hover:bg-red-50" data-testid={`delete-task-${task.id}`}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </Card>
            );})}
          </div>
        )}

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-lg" data-testid="task-dialog">
            <DialogHeader>
              <DialogTitle>{editingId ? 'تعديل المهمة' : 'إضافة مهمة جديدة'}</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
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
              <div>
                <Label>الموظف المسؤول</Label>
                <Select value={form.assigned_to} onValueChange={(v) => setForm({ ...form, assigned_to: v })}>
                  <SelectTrigger data-testid="task-assignee-select"><SelectValue placeholder="اختر الموظف" /></SelectTrigger>
                  <SelectContent>
                    {employees.map(emp => (<SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-2">
                <Checkbox id="requires_proof" data-testid="task-proof-checkbox" checked={form.requires_proof} onCheckedChange={(v) => setForm({ ...form, requires_proof: !!v })} />
                <Label htmlFor="requires_proof" className="cursor-pointer">يتطلب إثباتاً (صورة/ملف)</Label>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)} data-testid="cancel-task-btn">إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-task-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog open={dailyDialogOpen} onOpenChange={setDailyDialogOpen}>
          <DialogContent className="max-w-lg" data-testid="daily-task-dialog">
            <DialogHeader>
              <DialogTitle>{editingDailyId ? 'تعديل المهمة اليومية' : 'إضافة مهمة يومية'}</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleDailySubmit} className="space-y-4">
              <div>
                <Label>العنوان</Label>
                <Input data-testid="daily-title-input" required value={dailyForm.title} onChange={(e) => setDailyForm({ ...dailyForm, title: e.target.value })} />
              </div>
              <div>
                <Label>الوصف</Label>
                <Textarea data-testid="daily-description-input" required value={dailyForm.description} onChange={(e) => setDailyForm({ ...dailyForm, description: e.target.value })} rows={3} />
              </div>
              <div>
                <Label>الموظفون</Label>
                <div className="mt-1 border border-gray-200 rounded-sm p-3 max-h-32 overflow-y-auto space-y-2">
                  {employees.map((emp) => (
                    <div key={emp.id} className="flex items-center gap-2">
                      <Checkbox
                        id={`daily-emp-${emp.id}`}
                        data-testid={`daily-emp-${emp.id}`}
                        checked={dailyForm.assigned_to.includes(emp.id)}
                        onCheckedChange={() => toggleDailyEmployee(emp.id)}
                      />
                      <Label htmlFor={`daily-emp-${emp.id}`} className="cursor-pointer font-normal">{emp.name}</Label>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <Label>وقت التنفيذ (اختياري)</Label>
                <Input data-testid="daily-time-input" type="time" value={dailyForm.execution_time} onChange={(e) => setDailyForm({ ...dailyForm, execution_time: e.target.value })} />
              </div>
              <div className="flex items-center gap-2">
                <Checkbox id="daily-requires-proof" data-testid="daily-proof-checkbox" checked={dailyForm.requires_proof} onCheckedChange={(v) => setDailyForm({ ...dailyForm, requires_proof: !!v })} />
                <Label htmlFor="daily-requires-proof" className="cursor-pointer">يتطلب إثباتاً (صورة) - اختياري</Label>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDailyDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-daily-task-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <CreateUrgentTaskDialog
        open={urgentDialogOpen}
        onOpenChange={setUrgentDialogOpen}
        employees={employees}
        onCreated={() => fetchAll(true)}
      />
    </Layout>
  );
};

export default OwnerTasks;
