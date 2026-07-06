import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { CheckSquare, Upload } from 'lucide-react';
import { toast } from 'sonner';

const PRIORITY_COLORS = {
  high: 'bg-red-50 text-red-700 border-red-200',
  medium: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  low: 'bg-blue-50 text-blue-700 border-blue-200',
};
const STATUS_COLORS = {
  new: 'bg-blue-50 text-blue-700 border-blue-200',
  in_progress: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending_review: 'bg-purple-50 text-purple-700 border-purple-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  rejected: 'bg-red-50 text-red-700 border-red-200',
  overdue: 'bg-red-50 text-red-700 border-red-200',
};
const STATUS_LABELS = { new: 'جديدة', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة', completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة' };
const PRIORITY_LABELS = { high: 'عالية', medium: 'متوسطة', low: 'منخفضة' };

const EmployeeTasks = ({ onLogout, language, setLanguage }) => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedTask, setSelectedTask] = useState(null);
  const [statusDialogOpen, setStatusDialogOpen] = useState(false);
  const [newStatus, setNewStatus] = useState('');

  useEffect(() => { fetchTasks(); }, []);

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const res = await api.get('/employee/tasks');
      setTasks(res.data);
    } catch (e) { toast.error('خطأ في جلب المهام'); }
    setLoading(false);
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
          <div className="grid gap-4">
            {tasks.map((task) => (
              <Card key={task.id} className="p-6 bg-white border border-gray-200 rounded-md task-card" data-testid={`emp-task-${task.id}`}>
                <div className="flex justify-between items-start gap-4 mb-3">
                  <div className="flex-1">
                    <div className="flex items-center flex-wrap gap-2 mb-2">
                      <h3 className="text-lg font-medium text-[#0A0A0A]">{task.title}</h3>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[task.priority]}`}>{PRIORITY_LABELS[task.priority]}</span>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[task.status]}`}>{STATUS_LABELS[task.status]}</span>
                    </div>
                    <p className="text-sm text-gray-600 mb-2">{task.description}</p>
                    <p className="text-xs text-gray-500">موعد التسليم: <b>{task.due_date}</b></p>
                    {task.requires_proof && <p className="text-xs text-orange-600 mt-1">⚠ تتطلب إثباتاً</p>}
                    {task.proof_files && task.proof_files.length > 0 && (
                      <div className="mt-2 flex gap-2 flex-wrap">
                        {task.proof_files.map((f, i) => (
                          f.startsWith('data:image') ?
                            <img key={i} src={f} alt="proof" className="w-16 h-16 object-cover rounded-sm border border-gray-200" /> :
                            <div key={i} className="text-xs bg-gray-100 px-2 py-1 rounded-sm">ملف {i+1}</div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
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
                  </div>
                </div>
              </Card>
            ))}
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
      </div>
    </Layout>
  );
};

export default EmployeeTasks;
