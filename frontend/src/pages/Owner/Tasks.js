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
import { Plus, Pencil, Trash2, CheckSquare } from 'lucide-react';
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

const STATUS_LABELS_AR = {
  new: 'جديدة', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة',
  completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة'
};
const PRIORITY_LABELS_AR = { high: 'عالية', medium: 'متوسطة', low: 'منخفضة' };

const OwnerTasks = ({ onLogout, language, setLanguage }) => {
  const [tasks, setTasks] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({
    title: '', description: '', priority: 'medium',
    assigned_to: '', due_date: '', requires_proof: false
  });

  useEffect(() => { fetchAll(); }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [tasksRes, empRes] = await Promise.all([
        api.get('/owner/tasks'),
        api.get('/owner/employees')
      ]);
      setTasks(tasksRes.data);
      setEmployees(empRes.data);
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
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

  const handleDelete = async (id) => {
    if (!window.confirm('هل أنت متأكد من حذف المهمة؟')) return;
    try {
      await api.delete(`/owner/tasks/${id}`);
      toast.success('تم الحذف بنجاح');
      fetchAll();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="tasks-title">
              {t('tasks', language)}
            </h1>
            <p className="text-gray-600 mt-2">إدارة المهام</p>
          </div>
          <Button data-testid="add-task-btn" onClick={openAdd} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
            <Plus className="w-4 h-4 me-2" /> إضافة مهمة
          </Button>
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
              <Card key={task.id} className="p-6 bg-white border border-gray-200 rounded-md task-card" data-testid={`task-card-${task.id}`}>
                <div className="flex justify-between items-start gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <h3 className="text-lg font-medium text-[#0A0A0A]">{task.title}</h3>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[task.priority]}`}>
                        {PRIORITY_LABELS_AR[task.priority]}
                      </span>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[task.status]}`}>
                        {STATUS_LABELS_AR[task.status]}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 mb-2">{task.description}</p>
                    <div className="flex flex-wrap gap-4 text-xs text-gray-500">
                      <span>الموظف: <b>{task.assigned_to_name || '-'}</b></span>
                      <span>موعد التسليم: <b>{task.due_date}</b></span>
                      {task.requires_proof && <span className="text-orange-600">يتطلب إثباتاً</span>}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(task)} data-testid={`edit-task-${task.id}`}>
                      <Pencil className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(task.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-task-${task.id}`}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
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
      </div>
    </Layout>
  );
};

export default OwnerTasks;
