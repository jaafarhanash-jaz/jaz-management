import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, Pencil, Trash2, Users as UsersIcon } from 'lucide-react';
import { toast } from 'sonner';

const OwnerEmployees = ({ onLogout, language, setLanguage }) => {
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState({
    name: '', email: '', phone: '', password: '',
    department: '', position: '', role: 'employee'
  });

  useEffect(() => { fetchEmployees(); }, []);

  const fetchEmployees = async () => {
    setLoading(true);
    try {
      const res = await api.get('/owner/employees');
      setEmployees(res.data);
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
  };

  const resetForm = () => {
    setForm({ name: '', email: '', phone: '', password: '', department: '', position: '', role: 'employee' });
    setEditingId(null);
  };

  const openAdd = () => { resetForm(); setDialogOpen(true); };

  const openEdit = (emp) => {
    setEditingId(emp.id);
    setForm({
      name: emp.name || '', email: emp.email || '', phone: emp.phone || '',
      password: '', department: emp.department || '', position: emp.position || '', role: 'employee'
    });
    setDialogOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingId) {
        const updates = { ...form };
        if (!updates.password) delete updates.password;
        delete updates.role;
        await api.put(`/owner/employees/${editingId}`, updates);
        toast.success('تم التحديث بنجاح');
      } else {
        await api.post('/owner/employees', form);
        toast.success('تمت الإضافة بنجاح');
      }
      setDialogOpen(false);
      resetForm();
      fetchEmployees();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('هل أنت متأكد من حذف الموظف؟')) return;
    try {
      await api.delete(`/owner/employees/${id}`);
      toast.success('تم الحذف بنجاح');
      fetchEmployees();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employees-title">
              {t('employees', language)}
            </h1>
            <p className="text-gray-600 mt-2">إدارة موظفي الشركة</p>
          </div>
          <Button data-testid="add-employee-btn" onClick={openAdd} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
            <Plus className="w-4 h-4 me-2" /> {t('add', language)}
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : employees.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <UsersIcon className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا يوجد موظفين حالياً</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">الاسم</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">البريد</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">الهاتف</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">القسم</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">المنصب</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">الإجراءات</th>
                  </tr>
                </thead>
                <tbody>
                  {employees.map((emp) => (
                    <tr key={emp.id} className="border-b border-gray-100 hover:bg-gray-50/50 employee-row" data-testid={`employee-row-${emp.id}`}>
                      <td className="px-6 py-4 font-medium text-[#0A0A0A]">{emp.name}</td>
                      <td className="px-6 py-4 text-gray-600">{emp.email}</td>
                      <td className="px-6 py-4 text-gray-600">{emp.phone}</td>
                      <td className="px-6 py-4 text-gray-600">{emp.department || '-'}</td>
                      <td className="px-6 py-4 text-gray-600">{emp.position || '-'}</td>
                      <td className="px-6 py-4">
                        <div className="flex gap-2">
                          <Button variant="ghost" size="sm" onClick={() => openEdit(emp)} data-testid={`edit-employee-${emp.id}`}>
                            <Pencil className="w-4 h-4" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDelete(emp.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-employee-${emp.id}`}>
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md" data-testid="employee-dialog">
            <DialogHeader>
              <DialogTitle>{editingId ? 'تعديل الموظف' : 'إضافة موظف'}</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <Label>الاسم</Label>
                <Input data-testid="emp-name-input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div>
                <Label>البريد الإلكتروني</Label>
                <Input data-testid="emp-email-input" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} disabled={!!editingId} />
              </div>
              <div>
                <Label>الهاتف</Label>
                <Input data-testid="emp-phone-input" required value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </div>
              {!editingId && (
                <div>
                  <Label>كلمة المرور</Label>
                  <Input data-testid="emp-password-input" type="password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
                </div>
              )}
              <div>
                <Label>القسم</Label>
                <Input data-testid="emp-department-input" value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} />
              </div>
              <div>
                <Label>المنصب</Label>
                <Input data-testid="emp-position-input" value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} />
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)} data-testid="cancel-emp-btn">{t('cancel', language)}</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-emp-btn">{t('save', language)}</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default OwnerEmployees;
