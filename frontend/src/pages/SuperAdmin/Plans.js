import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { validateAndFocus } from '@/utils/formValidation';
import { Plus, Pencil, Trash2, CreditCard, Users } from 'lucide-react';
import { toast } from 'sonner';

const SuperAdminPlans = ({ onLogout, language, setLanguage }) => {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: '', max_employees: 10, price: 0, duration_months: 1, features: '' });
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({ name: '', max_employees: 10, price: 0, duration_months: 1, features: '', is_active: true });

  useEffect(() => { fetch(); }, []);

  const fetch = async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/subscription-plans');
      setPlans(res.data);
    } catch (e) { toast.error('خطأ'); }
    setLoading(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    try {
      const payload = {
        ...form,
        max_employees: parseInt(form.max_employees),
        price: parseFloat(form.price),
        duration_months: parseInt(form.duration_months),
        features: form.features.split(',').map(f => f.trim()).filter(Boolean)
      };
      await api.post('/admin/subscription-plans', payload);
      toast.success('تمت الإضافة');
      setDialogOpen(false);
      setForm({ name: '', max_employees: 10, price: 0, duration_months: 1, features: '' });
      fetch();
    } catch (err) { toast.error('حدث خطأ'); }
  };

  const toggleActive = async (plan) => {
    try {
      await api.put(`/admin/subscription-plans/${plan.id}`, { is_active: !plan.is_active });
      toast.success('تم التحديث');
      fetch();
    } catch (e) { toast.error('حدث خطأ'); }
  };

  const openEdit = (plan) => {
    setEditingId(plan.id);
    setEditForm({
      name: plan.name,
      max_employees: plan.max_employees,
      price: plan.price,
      duration_months: plan.duration_months,
      features: (plan.features || []).join(', '),
      is_active: plan.is_active
    });
    setEditDialogOpen(true);
  };

  const handleEditSubmit = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    try {
      const payload = {
        name: editForm.name,
        max_employees: parseInt(editForm.max_employees),
        price: parseFloat(editForm.price),
        duration_months: parseInt(editForm.duration_months),
        features: editForm.features.split(',').map(f => f.trim()).filter(Boolean),
        is_active: editForm.is_active
      };
      await api.put(`/admin/subscription-plans/${editingId}`, payload);
      toast.success('تم التحديث');
      setEditDialogOpen(false);
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('هل أنت متأكد من حذف هذه الخطة؟')) return;
    try {
      await api.delete(`/admin/subscription-plans/${id}`);
      toast.success('تم الحذف');
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="plans-title">{t('plans', language)}</h1>
          <Button data-testid="add-plan-btn" onClick={() => setDialogOpen(true)} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
            <Plus className="w-4 h-4 me-2" />إضافة خطة
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : plans.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <CreditCard className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد خطط</p>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {plans.map((p) => (
              <Card key={p.id} className="p-6 bg-white border border-gray-200 rounded-md stat-card" data-testid={`plan-card-${p.id}`}>
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h3 className="text-lg font-bold text-[#0A0A0A]">{p.name}</h3>
                    <p className="text-3xl font-bold text-[#0033A0] mt-2">${p.price}<span className="text-sm text-gray-500">/{p.duration_months}ش</span></p>
                  </div>
                  <button onClick={() => toggleActive(p)} data-testid={`toggle-plan-${p.id}`} className={`px-2.5 py-0.5 rounded-full text-xs font-medium border cursor-pointer ${
                    p.is_active ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-50 text-gray-700 border-gray-200'
                  }`}>
                    {p.is_active ? 'نشطة' : 'موقوفة'}
                  </button>
                </div>
                <div className="flex items-center gap-2 text-sm text-gray-600 mb-3">
                  <Users className="w-4 h-4" /> حتى {p.max_employees} موظف
                </div>
                {p.features && p.features.length > 0 && (
                  <ul className="space-y-1 text-xs text-gray-600">
                    {p.features.map((f, i) => <li key={i}>✓ {f}</li>)}
                  </ul>
                )}
                <div className="flex gap-2 mt-4 pt-3 border-t border-gray-100">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(p)} data-testid={`edit-plan-${p.id}`}>
                    <Pencil className="w-4 h-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(p.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-plan-${p.id}`}>
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md" data-testid="plan-dialog">
            <DialogHeader><DialogTitle>إضافة خطة جديدة</DialogTitle></DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div><Label>اسم الخطة</Label><Input data-testid="p-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>الحد الأقصى للموظفين</Label><Input data-testid="p-max-emp" type="number" min="1" required value={form.max_employees} onChange={(e) => setForm({ ...form, max_employees: e.target.value })} /></div>
                <div><Label>السعر ($)</Label><Input data-testid="p-price" type="number" step="0.01" min="0" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></div>
              </div>
              <div><Label>المدة (شهور)</Label><Input data-testid="p-duration" type="number" min="1" required value={form.duration_months} onChange={(e) => setForm({ ...form, duration_months: e.target.value })} /></div>
              <div><Label>المميزات (مفصولة بفاصلة)</Label><Input data-testid="p-features" placeholder="ميزة 1, ميزة 2" value={form.features} onChange={(e) => setForm({ ...form, features: e.target.value })} /></div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-plan-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
          <DialogContent className="max-w-md" data-testid="edit-plan-dialog">
            <DialogHeader><DialogTitle>تعديل الخطة</DialogTitle></DialogHeader>
            <form onSubmit={handleEditSubmit} className="space-y-3">
              <div><Label>اسم الخطة</Label><Input data-testid="ep-name" required value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>الحد الأقصى للموظفين</Label><Input data-testid="ep-max-emp" type="number" min="1" required value={editForm.max_employees} onChange={(e) => setEditForm({ ...editForm, max_employees: e.target.value })} /></div>
                <div><Label>السعر ($)</Label><Input data-testid="ep-price" type="number" step="0.01" min="0" required value={editForm.price} onChange={(e) => setEditForm({ ...editForm, price: e.target.value })} /></div>
              </div>
              <div><Label>المدة (شهور)</Label><Input data-testid="ep-duration" type="number" min="1" required value={editForm.duration_months} onChange={(e) => setEditForm({ ...editForm, duration_months: e.target.value })} /></div>
              <div><Label>المميزات (مفصولة بفاصلة)</Label><Input data-testid="ep-features" placeholder="ميزة 1, ميزة 2" value={editForm.features} onChange={(e) => setEditForm({ ...editForm, features: e.target.value })} /></div>
              <div className="flex items-center gap-2">
                <input type="checkbox" id="ep-is-active" data-testid="ep-is-active" checked={editForm.is_active} onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })} />
                <Label htmlFor="ep-is-active" className="cursor-pointer">نشطة</Label>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-edit-plan-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default SuperAdminPlans;
