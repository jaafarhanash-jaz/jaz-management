import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { CheckSquare, Upload, Siren, Repeat, PlayCircle, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';

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
const STATUS_LABELS = { new: 'معلقة', received: 'تم الاستلام', seen: 'تمت المشاهدة', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة', completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة', cancelled: 'ملغاة' };
const PRIORITY_LABELS = { critical: 'عاجلة', high: 'عالية', medium: 'متوسطة', low: 'منخفضة' };

const playAlertSound = () => {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    [0, 0.25].forEach((delay) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.type = 'sine';
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.3, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.4);
      oscillator.start(ctx.currentTime + delay);
      oscillator.stop(ctx.currentTime + delay + 0.4);
    });
  } catch (e) { /* Web Audio not available - fail silently */ }
};

const EmployeeTasks = ({ onLogout, language, setLanguage }) => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedTask, setSelectedTask] = useState(null);
  const [statusDialogOpen, setStatusDialogOpen] = useState(false);
  const [newStatus, setNewStatus] = useState('');
  const [confirmAction, setConfirmAction] = useState(null);
  const knownUrgentIds = useRef(null);

  // The Employee Dashboard's "Quick Open" links here with ?highlight=<id> -
  // scrolls to and briefly outlines that task card so the click feels like
  // it actually landed somewhere, instead of dropping the employee onto an
  // undifferentiated list they have to re-scan.
  const [searchParams] = useSearchParams();
  const highlightId = searchParams.get('highlight');

  useEffect(() => {
    fetchTasks();
    const interval = setInterval(() => fetchTasks(true), 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!highlightId || tasks.length === 0) return;
    const el = document.getElementById(`task-${highlightId}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [highlightId, tasks]);

  const fetchTasks = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/employee/tasks');
      const urgentIds = new Set(res.data.filter((t) => t.task_category === 'urgent').map((t) => t.id));

      if (knownUrgentIds.current !== null) {
        const newlyArrived = [...urgentIds].filter((id) => !knownUrgentIds.current.has(id));
        if (newlyArrived.length > 0) {
          const task = res.data.find((t) => t.id === newlyArrived[0]);
          toast.error(`مهمة جديدة: ${task?.title || ''}`, { duration: 8000 });
          playAlertSound();
        }
      }
      knownUrgentIds.current = urgentIds;

      setTasks(res.data);
    } catch (e) { if (!silent) toast.error('خطأ في جلب المهام'); }
    if (!silent) setLoading(false);
  };

  const openStatus = (task) => {
    setSelectedTask(task);
    setNewStatus(task.status);
    setStatusDialogOpen(true);
  };

  const updateStatus = async () => {
    if (selectedTask.requires_proof && newStatus === 'completed' && (!selectedTask.proof_files || selectedTask.proof_files.length === 0)) {
      toast.error('يجب رفع إثبات قبل إكمال هذه المهمة');
      return;
    }
    try {
      await api.put(`/employee/tasks/${selectedTask.id}/status`, { status: newStatus });
      toast.success('تم تحديث الحالة');
      setStatusDialogOpen(false);
      fetchTasks();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  const handleFileUpload = async (task, e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await api.post(`/employee/tasks/${task.id}/proof`, { file_url: reader.result });
        toast.success('تم رفع الإثبات');
        fetchTasks();
      } catch (err) { toast.error('حدث خطأ'); }
    };
    reader.readAsDataURL(file);
  };

  const handleConfirmAction = async () => {
    const { type, task } = confirmAction;
    if (type === 'complete' && task.requires_proof && (!task.proof_files || task.proof_files.length === 0)) {
      toast.error('يجب رفع إثبات قبل إكمال هذه المهمة');
      setConfirmAction(null);
      return;
    }
    try {
      await api.post(`/employee/tasks/${task.id}/${type}`);
      toast.success(type === 'start' ? 'تم بدء المهمة' : 'تم إكمال المهمة');
      setConfirmAction(null);
      fetchTasks();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
      setConfirmAction(null);
    }
  };

  const renderTaskCard = (task, highlight = false) => (
    <Card
      key={task.id}
      id={`task-${task.id}`}
      className={`p-6 border rounded-md task-card transition-shadow ${highlight ? 'bg-red-50/40 border-red-200' : 'bg-white border-gray-200'} ${highlightId === task.id ? 'ring-2 ring-[#0033A0] ring-offset-2' : ''}`}
      data-testid={`emp-task-${task.id}`}
    >
      <div className="flex justify-between items-start gap-4 mb-3">
        <div className="flex-1">
          <div className="flex items-center flex-wrap gap-2 mb-2">
            <h3 className="text-lg font-medium text-[#0A0A0A]">{task.title}</h3>
            <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[task.priority]}`}>{PRIORITY_LABELS[task.priority]}</span>
            <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[task.status]}`}>{STATUS_LABELS[task.status] || task.status}</span>
          </div>
          <p className="text-sm text-gray-600 mb-2">{task.description}</p>
          <p className="text-xs text-gray-500">موعد التسليم: <b>{task.due_date}{task.due_time ? ` ${task.due_time}` : ''}</b></p>
          {task.execution_date && <p className="text-xs text-gray-500">وقت التنفيذ: <b>{task.execution_date} {task.execution_time}</b></p>}
          {task.requires_proof && <p className="text-xs text-orange-600 mt-1">⚠ تتطلب إثباتاً</p>}
          {task.proof_files && task.proof_files.length > 0 && (
            <div className="mt-2 flex gap-2 flex-wrap">
              {task.proof_files.map((f, i) => (
                f.startsWith('data:image') ?
                  <img key={i} src={f} alt="proof" className="w-16 h-16 object-cover rounded-sm border border-gray-200" /> :
                  <div key={i} className="text-xs bg-gray-100 px-2 py-1 rounded-sm">ملف {i + 1}</div>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          {task.task_category ? (
            <>
              {(task.status === 'new' || task.status === 'seen') && (
                <Button size="sm" onClick={() => setConfirmAction({ type: 'start', task })} className="bg-[#0033A0] hover:bg-[#002277]" data-testid={`start-task-${task.id}`}>
                  <PlayCircle className="w-4 h-4 me-1" /> بدء المهمة
                </Button>
              )}
              {task.status === 'in_progress' && (
                <Button size="sm" onClick={() => setConfirmAction({ type: 'complete', task })} className="bg-green-600 hover:bg-green-700 text-white" data-testid={`complete-task-${task.id}`}>
                  <CheckCircle2 className="w-4 h-4 me-1" /> إكمال المهمة
                </Button>
              )}
              {task.requires_proof && task.status === 'in_progress' && (
                <label className="cursor-pointer">
                  <input type="file" className="hidden" accept="image/*,.pdf" onChange={(e) => handleFileUpload(task, e)} data-testid={`upload-proof-${task.id}`} />
                  <Button size="sm" variant="outline" asChild>
                    <span><Upload className="w-4 h-4 me-1" /> رفع إثبات</span>
                  </Button>
                </label>
              )}
            </>
          ) : (
            <>
              <Button size="sm" onClick={() => openStatus(task)} className="bg-[#0033A0] hover:bg-[#002277]" data-testid={`update-status-${task.id}`}>
                تحديث الحالة
              </Button>
              {task.requires_proof && (
                <label className="cursor-pointer">
                  <input type="file" className="hidden" accept="image/*,.pdf" onChange={(e) => handleFileUpload(task, e)} data-testid={`upload-proof-${task.id}`} />
                  <Button size="sm" variant="outline" asChild>
                    <span><Upload className="w-4 h-4 me-1" /> رفع إثبات</span>
                  </Button>
                </label>
              )}
            </>
          )}
        </div>
      </div>
    </Card>
  );

  const urgentTasks = tasks.filter((t) => t.task_category === 'urgent');
  const dailyTasks = tasks.filter((t) => t.task_category === 'daily');
  const otherTasks = tasks.filter((t) => !t.task_category);

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-tasks-title">
            {t('tasks', language)}
          </h1>
          <p className="text-gray-600 mt-2">مهامي</p>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : tasks.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <CheckSquare className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد مهام حالياً</p>
          </Card>
        ) : (
          <div className="space-y-8">
            {urgentTasks.length > 0 && (
              <div data-testid="urgent-tasks-section">
                <div className="flex items-center gap-2 mb-3">
                  <Siren className="w-5 h-5 text-red-600" />
                  <h2 className="text-xl font-bold text-red-600">المهام الفورية</h2>
                </div>
                <div className="grid gap-4">{urgentTasks.map((t) => renderTaskCard(t, true))}</div>
              </div>
            )}

            {dailyTasks.length > 0 && (
              <div data-testid="daily-tasks-section">
                <div className="flex items-center gap-2 mb-3">
                  <Repeat className="w-5 h-5 text-indigo-600" />
                  <h2 className="text-xl font-bold text-[#0A0A0A]">📋 المهام اليومية</h2>
                </div>
                <div className="grid gap-4">{dailyTasks.map((t) => renderTaskCard(t))}</div>
              </div>
            )}

            {otherTasks.length > 0 && (
              <div data-testid="other-tasks-section">
                <h2 className="text-xl font-bold text-[#0A0A0A] mb-3">مهام أخرى</h2>
                <div className="grid gap-4">{otherTasks.map((t) => renderTaskCard(t))}</div>
              </div>
            )}
          </div>
        )}

        <Dialog open={statusDialogOpen} onOpenChange={setStatusDialogOpen}>
          <DialogContent className="max-w-sm" data-testid="status-dialog">
            <DialogHeader><DialogTitle>تحديث حالة المهمة</DialogTitle></DialogHeader>
            <Select value={newStatus} onValueChange={setNewStatus}>
              <SelectTrigger data-testid="status-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="new">جديدة</SelectItem>
                <SelectItem value="in_progress">قيد التنفيذ</SelectItem>
                <SelectItem value="pending_review">بانتظار المراجعة</SelectItem>
                <SelectItem value="completed">مكتملة</SelectItem>
              </SelectContent>
            </Select>
            <DialogFooter>
              <Button variant="outline" onClick={() => setStatusDialogOpen(false)}>إلغاء</Button>
              <Button onClick={updateStatus} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-status-btn">حفظ</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={!!confirmAction} onOpenChange={(open) => !open && setConfirmAction(null)}>
          <DialogContent className="max-w-sm" data-testid="confirm-action-dialog">
            <DialogHeader>
              <DialogTitle>{confirmAction?.type === 'start' ? 'بدء المهمة' : 'إكمال المهمة'}</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-gray-600">
              {confirmAction?.type === 'start'
                ? 'هل أنت متأكد من أنك تريد بدء هذه المهمة؟'
                : 'هل أنت متأكد من أنك تريد إكمال هذه المهمة؟'}
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmAction(null)} data-testid="confirm-cancel-btn">إلغاء</Button>
              <Button onClick={handleConfirmAction} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="confirm-confirm-btn">تأكيد</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default EmployeeTasks;
