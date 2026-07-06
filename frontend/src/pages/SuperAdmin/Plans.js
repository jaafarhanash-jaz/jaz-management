import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, CreditCard, Users } from 'lucide-react';
import { toast } from 'sonner';

const SuperAdminPlans = ({ onLogout, language, setLanguage }) => {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: '', max_employees: 10, price: 0, duration_months: 1, features: '' });

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
                <div><Label>الحد الأقصى للموظفين</Label><Input data-testid="p-max-emp" type="number" required value={form.max_employees} onChange={(e) => setForm({ ...form, max_employees: e.target.value })} /></div>
                <div><Label>السعر ($)</Label><Input data-testid="p-price" type="number" step="0.01" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></div>
              </div>
              <div><Label>المدة (شهور)</Label><Input data-testid="p-duration" type="number" required value={form.duration_months} onChange={(e) => setForm({ ...form, duration_months: e.target.value })} /></div>
              <div><Label>المميزات (مفصولة بفاصلة)</Label><Input data-testid="p-features" placeholder="ميزة 1, ميزة 2" value={form.features} onChange={(e) => setForm({ ...form, features: e.target.value })} /></div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-plan-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default SuperAdminPlans;
