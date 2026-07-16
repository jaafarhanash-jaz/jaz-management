import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Checkbox } from '@/components/ui/checkbox';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import {
  Plus, Pencil, Trash2, CheckSquare, Siren, Repeat, PauseCircle, PlayCircle, ChevronDown, ChevronUp,
  CalendarClock, GitBranch, X as XIcon, Archive, ArrowRight, UserCircle, Timer,
} from 'lucide-react';
import { toast } from 'sonner';
import CreateUrgentTaskDialog from '@/components/CreateUrgentTaskDialog';
import CreateTaskDialog from '@/components/CreateTaskDialog';

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
  scheduled: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  pending_sequence: 'bg-gray-50 text-gray-500 border-gray-200',
};

const STATUS_LABELS_AR = {
  new: 'جديدة (معلقة)', received: 'تم الاستلام', seen: 'شوهدت', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة',
  completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة', cancelled: 'ملغاة',
  scheduled: 'مجدولة', pending_sequence: 'بانتظار الدور',
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

// Shared by the active-tasks list and Task History (Do NOT duplicate logic):
// same badges/timeline for both, just with history-only fields (creator,
// completion date, completion duration) and no edit/delete actions when
// historyView is set - completed tasks are the permanent archive.
const TaskCard = ({ task, expanded, onToggleExpand, onEdit, onDelete, historyView = false }) => {
  const categoryBadge = getCategoryBadge(task);
  return (
    <Card className="p-6 bg-white border border-gray-200 rounded-md task-card" data-testid={`task-card-${task.id}`}>
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
            {!historyView && task.status === 'scheduled' && task.scheduled_activation_at && (
              <span className="text-indigo-600 flex items-center gap-1">
                <CalendarClock className="w-3 h-3" /> تُفعّل: {formatTime(task.scheduled_activation_at)}
              </span>
            )}
            {!historyView && task.sequence_order !== null && task.sequence_order !== undefined && (
              <span className="text-gray-500 flex items-center gap-1">
                <GitBranch className="w-3 h-3" /> خطوة {task.sequence_order + 1} من سير عمل تسلسلي
              </span>
            )}
          </div>
          {historyView && (
            <div className="flex flex-wrap gap-4 text-xs text-gray-500 mt-2 pt-2 border-t border-gray-100" data-testid={`history-details-${task.id}`}>
              <span className="flex items-center gap-1"><UserCircle className="w-3 h-3" /> بواسطة: <b>{task.created_by_name || '-'}</b></span>
              <span>تاريخ الإنشاء: <b>{formatTime(task.created_at)}</b></span>
              <span>تاريخ الإكمال: <b>{formatTime(task.completed_at)}</b></span>
              <span className="flex items-center gap-1"><Timer className="w-3 h-3" /> مدة الإنجاز: <b>{formatDuration(task.created_at, task.completed_at)}</b></span>
              {task.sequence_order !== null && task.sequence_order !== undefined && (
                <span className="flex items-center gap-1"><GitBranch className="w-3 h-3" /> خطوة {task.sequence_order + 1} من سير عمل تسلسلي</span>
              )}
            </div>
          )}
          {(task.task_category || historyView) && (
            <button onClick={() => onToggleExpand(task.id)} className="text-xs text-[#0033A0] mt-2 flex items-center gap-1" data-testid={`timeline-toggle-${task.id}`}>
              {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />} عرض السجل الزمني
            </button>
          )}
          {expanded && <TaskTimeline task={task} />}
        </div>
        {!historyView && (
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => onEdit(task)} data-testid={`edit-task-${task.id}`}>
              <Pencil className="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onDelete(task)} className="text-red-600 hover:bg-red-50" data-testid={`delete-task-${task.id}`}>
              <Trash2 className="w-4 h-4" />
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
};

const FILTER_LABELS = { all: 'الكل', urgent: 'المهام الفورية', critical: 'المهام العاجلة', overdue: 'المهام المتأخرة' };

const selectClass = 'h-9 rounded-md border border-input bg-white px-3 text-sm';

const HISTORY_SORT_OPTIONS = [
  { value: 'completed_desc', label: 'الأحدث إكمالاً' },
  { value: 'completed_asc', label: 'الأقدم إكمالاً' },
  { value: 'created_desc', label: 'الأحدث إنشاءً' },
  { value: 'employee_name', label: 'اسم الموظف' },
  { value: 'priority', label: 'الأولوية' },
];

// Completion duration (Task History) - derived purely client-side from the
// same created_at/completed_at fields the task already carries; no new
// backend field, matches the "reuse existing Task data" rule.
const formatDuration = (startIso, endIso) => {
  if (!startIso || !endIso) return '-';
  const ms = new Date(endIso) - new Date(startIso);
  if (!Number.isFinite(ms) || ms < 0) return '-';
  const totalMinutes = Math.floor(ms / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts = [];
  if (days) parts.push(`${days} يوم`);
  if (hours) parts.push(`${hours} ساعة`);
  if (minutes || parts.length === 0) parts.push(`${minutes} دقيقة`);
  return parts.join(' ');
};

const buildHistoryQuery = (f) => {
  const params = new URLSearchParams();
  if (f.search) params.set('search', f.search);
  if (f.employee_id) params.set('employee_id', f.employee_id);
  if (f.created_by) params.set('created_by', f.created_by);
  if (f.priority) params.set('priority', f.priority);
  if (f.created_from) params.set('created_from', f.created_from);
  if (f.created_to) params.set('created_to', f.created_to);
  if (f.completed_from) params.set('completed_from', f.completed_from);
  if (f.completed_to) params.set('completed_to', f.completed_to);
  if (f.sort) params.set('sort', f.sort);
  return params.toString();
};

const OwnerTasks = ({ onLogout, language, setLanguage }) => {
  const [tasks, setTasks] = useState([]);
  const [dailyTasks, setDailyTasks] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);

  // Dashboard widgets deep-link here (Part 1) - e.g. "Urgent Tasks"
  // navigates to ?view=urgent, "Critical Tasks" to ?view=critical. Purely a
  // client-side filter over the same list already fetched below - no new
  // backend query needed since the owner's full task list is small enough
  // to already be fetched in one call.
  const [searchParams, setSearchParams] = useSearchParams();
  const [quickFilter, setQuickFilter] = useState(searchParams.get('view') || 'all');
  useEffect(() => { setQuickFilter(searchParams.get('view') || 'all'); }, [searchParams]);
  const visibleTasks = tasks.filter((task) => {
    if (quickFilter === 'urgent') return task.task_category === 'urgent';
    if (quickFilter === 'critical') return task.priority === 'critical';
    if (quickFilter === 'overdue') return task.status === 'overdue';
    return true;
  });

  // Add/Edit Task dialog - CreateTaskDialog is the single canonical
  // implementation (also reused by Calendar.js), not duplicated here.
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [editingTask, setEditingTask] = useState(null);

  const [dailyDialogOpen, setDailyDialogOpen] = useState(false);
  const [editingDailyId, setEditingDailyId] = useState(null);
  const [dailyForm, setDailyForm] = useState({ title: '', description: '', assigned_to: [], execution_time: '', requires_proof: false });

  const [urgentDialogOpen, setUrgentDialogOpen] = useState(false);

  // Task History (permanent completed-task archive) - reuses the same
  // /owner/tasks table via a dedicated filtered/sorted endpoint. A view-mode
  // toggle inside this same page, not a separate route, per spec ("Inside
  // the existing Tasks page, add a new button").
  const [historyMode, setHistoryMode] = useState(false);
  const [historyTasks, setHistoryTasks] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyExpandedId, setHistoryExpandedId] = useState(null);
  const [historyFilters, setHistoryFilters] = useState({
    search: '', employee_id: '', created_by: '', priority: '',
    created_from: '', created_to: '', completed_from: '', completed_to: '', sort: 'completed_desc',
  });

  useEffect(() => {
    fetchAll();
    const interval = setInterval(() => fetchAll(true), 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchHistory = async () => {
    setHistoryLoading(true);
    try {
      const res = await api.get(`/owner/tasks/history?${buildHistoryQuery(historyFilters)}`);
      setHistoryTasks(res.data);
    } catch (e) { toast.error('خطأ في جلب سجل المهام'); }
    setHistoryLoading(false);
  };

  useEffect(() => {
    if (historyMode) fetchHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyMode, historyFilters]);

  // Creator filter options: derived from creators actually present in the
  // fetched history results (real users, not a fabricated list) - avoids a
  // dedicated "list possible creators" endpoint for what is, today, always
  // the company owner.
  const historyCreators = Array.from(
    new Map(historyTasks.map((t) => [t.created_by, t.created_by_name])).entries()
  ).filter(([id]) => !!id);

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

  const openAdd = () => { setEditingTask(null); setTaskDialogOpen(true); };
  const openEdit = (task) => { setEditingTask(task); setTaskDialogOpen(true); };

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
            <p className="text-gray-600 mt-2">{historyMode ? 'سجل المهام المكتملة (أرشيف دائم)' : 'إدارة المهام'}</p>
          </div>
          {!historyMode && (
            <div className="flex gap-2">
              <Button onClick={() => setUrgentDialogOpen(true)} className="bg-red-600 hover:bg-red-700 text-white rounded-sm" data-testid="tasks-emergency-btn">
                <Siren className="w-4 h-4 me-2" /> مهمة فورية
              </Button>
              <Button data-testid="add-task-btn" onClick={openAdd} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
                <Plus className="w-4 h-4 me-2" /> إضافة مهمة
              </Button>
            </div>
          )}
        </div>

        {/* Daily Recurring Tasks Panel */}
        {!historyMode && (
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
        )}

        {/* Filters row: active-tasks quick filters on one side, the
            Task History toggle button next to them per spec ("add a new
            button next to the current task filters/tabs"). */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          {!historyMode && tasks.length > 0 ? (
            <div className="flex flex-wrap gap-2" data-testid="task-quick-filters">
              {Object.entries(FILTER_LABELS).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  data-testid={`task-filter-${value}`}
                  onClick={() => setSearchParams(value === 'all' ? {} : { view: value })}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    quickFilter === value ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : <div />}
          <Button
            type="button"
            variant="outline"
            onClick={() => setHistoryMode((m) => !m)}
            className="rounded-sm"
            data-testid="task-history-toggle-btn"
          >
            {historyMode ? (
              <><ArrowRight className="w-4 h-4 me-2" /> العودة إلى المهام النشطة</>
            ) : (
              <><Archive className="w-4 h-4 me-2" /> سجل المهام</>
            )}
          </Button>
        </div>

        {!historyMode && (
          loading ? (
            <div className="text-center py-12 text-gray-500">Loading...</div>
          ) : visibleTasks.length === 0 ? (
            <Card className="p-12 text-center bg-white border border-gray-200">
              <CheckSquare className="w-12 h-12 mx-auto text-gray-300 mb-4" />
              <p className="text-gray-500">{tasks.length === 0 ? 'لا توجد مهام حالياً' : 'لا توجد مهام مطابقة لهذا الفلتر'}</p>
            </Card>
          ) : (
            <div className="grid gap-4">
              {visibleTasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  expanded={expandedId === task.id}
                  onToggleExpand={(id) => setExpandedId(expandedId === id ? null : id)}
                  onEdit={openEdit}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )
        )}

        {historyMode && (
          <div className="space-y-4" data-testid="task-history-panel">
            <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-4">
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">بحث بعنوان المهمة</label>
                  <Input
                    data-testid="history-search-input"
                    value={historyFilters.search}
                    placeholder="عنوان المهمة..."
                    onChange={(e) => setHistoryFilters({ ...historyFilters, search: e.target.value })}
                    className="w-48"
                  />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الموظف</label>
                  <select
                    data-testid="history-employee-filter"
                    className={selectClass}
                    value={historyFilters.employee_id}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, employee_id: e.target.value })}
                  >
                    <option value="">الكل</option>
                    {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">أنشئت بواسطة</label>
                  <select
                    data-testid="history-creator-filter"
                    className={selectClass}
                    value={historyFilters.created_by}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, created_by: e.target.value })}
                  >
                    <option value="">الكل</option>
                    {historyCreators.map(([id, name]) => <option key={id} value={id}>{name || id}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الأولوية</label>
                  <select
                    data-testid="history-priority-filter"
                    className={selectClass}
                    value={historyFilters.priority}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, priority: e.target.value })}
                  >
                    <option value="">الكل</option>
                    <option value="critical">عاجلة</option>
                    <option value="high">عالية</option>
                    <option value="medium">متوسطة</option>
                    <option value="low">منخفضة</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الترتيب</label>
                  <select
                    data-testid="history-sort-select"
                    className={selectClass}
                    value={historyFilters.sort}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, sort: e.target.value })}
                  >
                    {HISTORY_SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
              </div>
              <div className="flex flex-wrap items-end gap-3 mt-3 pt-3 border-t border-gray-100">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ الإنشاء من</label>
                  <Input data-testid="history-created-from" type="date" value={historyFilters.created_from}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, created_from: e.target.value })} className="w-40" />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ الإنشاء إلى</label>
                  <Input data-testid="history-created-to" type="date" value={historyFilters.created_to}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, created_to: e.target.value })} className="w-40" />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ الإكمال من</label>
                  <Input data-testid="history-completed-from" type="date" value={historyFilters.completed_from}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, completed_from: e.target.value })} className="w-40" />
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ الإكمال إلى</label>
                  <Input data-testid="history-completed-to" type="date" value={historyFilters.completed_to}
                    onChange={(e) => setHistoryFilters({ ...historyFilters, completed_to: e.target.value })} className="w-40" />
                </div>
                {(historyFilters.search || historyFilters.employee_id || historyFilters.created_by || historyFilters.priority ||
                  historyFilters.created_from || historyFilters.created_to || historyFilters.completed_from || historyFilters.completed_to) && (
                  <Button
                    type="button" variant="ghost" size="sm"
                    onClick={() => setHistoryFilters({ search: '', employee_id: '', created_by: '', priority: '', created_from: '', created_to: '', completed_from: '', completed_to: '', sort: historyFilters.sort })}
                    data-testid="history-clear-filters-btn"
                  >
                    <XIcon className="w-3.5 h-3.5 me-1" /> مسح الفلاتر
                  </Button>
                )}
              </div>
            </Card>

            {historyLoading ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : historyTasks.length === 0 ? (
              <Card className="p-12 text-center bg-white border border-gray-200">
                <Archive className="w-12 h-12 mx-auto text-gray-300 mb-4" />
                <p className="text-gray-500">لا توجد مهام مكتملة مطابقة لهذه الفلاتر</p>
              </Card>
            ) : (
              <div className="grid gap-4">
                {historyTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    historyView
                    expanded={historyExpandedId === task.id}
                    onToggleExpand={(id) => setHistoryExpandedId(historyExpandedId === id ? null : id)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        <CreateTaskDialog
          open={taskDialogOpen}
          onOpenChange={setTaskDialogOpen}
          employees={employees}
          editingTask={editingTask}
          onCreated={() => fetchAll(true)}
        />

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
