import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, Trash2, Building2 } from 'lucide-react';
import { toast } from 'sonner';

const SuperAdminCompanies = ({ onLogout, language, setLanguage }) => {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({
    name: '', owner_email: '', owner_name: '', owner_password: '', owner_phone: '', address: ''
  });

  useEffect(() => { fetch(); }, []);

  const fetch = async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/companies');
      setCompanies(res.data);
    } catch (e) { toast.error('خطأ'); }
    setLoading(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post('/admin/companies', form);
      toast.success('تمت إضافة الشركة');
      setDialogOpen(false);
      setForm({ name: '', owner_email: '', owner_name: '', owner_password: '', owner_phone: '', address: '' });
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const toggleStatus = async (company) => {
    try {
      const newStatus = company.subscription_status === 'active' ? 'inactive' : 'active';
      await api.put(`/admin/companies/${company.id}`, { subscription_status: newStatus });
      toast.success('تم التحديث');
      fetch();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('هل أنت متأكد من حذف الشركة؟')) return;
    try {
      await api.delete(`/admin/companies/${id}`);
      toast.success('تم الحذف');
      fetch();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="companies-title">{t('companies', language)}</h1>
          <Button data-testid="add-company-btn" onClick={() => setDialogOpen(true)} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
            <Plus className="w-4 h-4 me-2" />إضافة شركة
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : companies.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <Building2 className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد شركات</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الشركة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الموظفين</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الإجراءات</th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((c) => (
                    <tr key={c.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`company-row-${c.id}`}>
                      <td className="px-6 py-4">
                        <p className="font-medium text-[#0A0A0A]">{c.name}</p>
                        <p className="text-xs text-gray-500">{c.address}</p>
                      </td>
                      <td className="px-6 py-4">{c.employee_count}</td>
                      <td className="px-6 py-4">
                        <button onClick={() => toggleStatus(c)} data-testid={`toggle-status-${c.id}`}
                          className={`px-2.5 py-0.5 rounded-full text-xs font-medium border cursor-pointer ${
                            c.subscription_status === 'active' ? 'bg-green-50 text-green-700 border-green-200' :
                            'bg-gray-50 text-gray-700 border-gray-200'
                          }`}>
                          {c.subscription_status === 'active' ? 'نشط' : 'متوقف'}
                        </button>
                      </td>
                      <td className="px-6 py-4">
                        <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-company-${c.id}`}>
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md" data-testid="company-dialog">
            <DialogHeader><DialogTitle>إضافة شركة جديدة</DialogTitle></DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div><Label>اسم الشركة</Label><Input data-testid="c-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
              <div><Label>العنوان</Label><Input data-testid="c-address" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></div>
              <div className="pt-2 border-t"><p className="text-xs text-gray-500 uppercase tracking-wider mb-2">بيانات المالك</p></div>
              <div><Label>الاسم</Label><Input data-testid="c-owner-name" required value={form.owner_name} onChange={(e) => setForm({ ...form, owner_name: e.target.value })} /></div>
              <div><Label>البريد الإلكتروني</Label><Input data-testid="c-owner-email" type="email" required value={form.owner_email} onChange={(e) => setForm({ ...form, owner_email: e.target.value })} /></div>
              <div><Label>الهاتف</Label><Input data-testid="c-owner-phone" required value={form.owner_phone} onChange={(e) => setForm({ ...form, owner_phone: e.target.value })} /></div>
              <div><Label>كلمة المرور</Label><Input data-testid="c-owner-password" type="password" required value={form.owner_password} onChange={(e) => setForm({ ...form, owner_password: e.target.value })} /></div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-company-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default SuperAdminCompanies;
