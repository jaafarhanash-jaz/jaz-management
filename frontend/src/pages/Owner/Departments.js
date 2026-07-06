import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, Building } from 'lucide-react';
import { toast } from 'sonner';

const OwnerDepartments = ({ onLogout, language, setLanguage }) => {
  const [departments, setDepartments] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: '', head_id: '' });

  useEffect(() => { fetchAll(); }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [deptRes, empRes] = await Promise.all([
        api.get('/owner/departments'),
        api.get('/owner/employees')
      ]);
      setDepartments(deptRes.data);
      setEmployees(empRes.data);
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post('/owner/departments', form);
      toast.success('تمت الإضافة بنجاح');
      setDialogOpen(false);
      setForm({ name: '', head_id: '' });
      fetchAll();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="departments-title">
              {t('departments', language)}
            </h1>
            <p className="text-gray-600 mt-2">إدارة الأقسام</p>
          </div>
          <Button data-testid="add-department-btn" onClick={() => setDialogOpen(true)} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
            <Plus className="w-4 h-4 me-2" /> إضافة قسم
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : departments.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <Building className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد أقسام حالياً</p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {departments.map((dept) => (
              <Card key={dept.id} className="p-6 bg-white border border-gray-200 rounded-md stat-card" data-testid={`dept-card-${dept.id}`}>
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-lg font-medium text-[#0A0A0A]">{dept.name}</h3>
                    <p className="text-sm text-gray-500 mt-1">
                      المسؤول: <b>{dept.head_name || 'غير محدد'}</b>
                    </p>
                  </div>
                  <div className="p-2 rounded-sm bg-blue-50">
                    <Building className="w-5 h-5 text-[#0033A0]" />
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md" data-testid="dept-dialog">
            <DialogHeader>
              <DialogTitle>إضافة قسم جديد</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <Label>اسم القسم</Label>
                <Input data-testid="dept-name-input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div>
                <Label>رئيس القسم</Label>
                <Select value={form.head_id} onValueChange={(v) => setForm({ ...form, head_id: v })}>
                  <SelectTrigger data-testid="dept-head-select"><SelectValue placeholder="اختر (اختياري)" /></SelectTrigger>
                  <SelectContent>
                    {employees.map(emp => (<SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>))}
                  </SelectContent>
                </Select>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-dept-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default OwnerDepartments;
