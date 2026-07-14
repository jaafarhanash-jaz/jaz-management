import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus, Pencil, Trash2, Building2, PlayCircle, PauseCircle, RotateCcw, RefreshCw, ChevronsUpDown, Check } from 'lucide-react';
import { toast } from 'sonner';

const formatCurrency = (value) => `$${Number(value || 0).toFixed(2)}`;

const PlanCombobox = ({ plans, value, onChange, testId }) => {
  const [open, setOpen] = useState(false);
  const selected = plans.find((p) => p.id === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" role="combobox" data-testid={testId}
          className="w-full justify-between font-normal">
          {selected ? `${selected.name} - ${formatCurrency(selected.price)}` : 'اختر خطة الاشتراك...'}
          <ChevronsUpDown className="w-4 h-4 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-full p-0" align="start">
        <Command>
          <CommandInput placeholder="ابحث عن خطة..." />
          <CommandList>
            <CommandEmpty>لا توجد خطط نشطة</CommandEmpty>
            <CommandGroup>
              {plans.map((plan) => (
                <CommandItem
                  key={plan.id}
                  value={plan.name}
                  onSelect={() => { onChange(plan.id); setOpen(false); }}
                  data-testid={`${testId}-option-${plan.id}`}
                >
                  <Check className={`w-4 h-4 ${value === plan.id ? 'opacity-100' : 'opacity-0'}`} />
                  {plan.name} - {formatCurrency(plan.price)} ({plan.max_employees} موظف)
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};

const getRemainingDays = (endDate) => {
  if (!endDate) return null;
  const end = new Date(endDate);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  end.setHours(0, 0, 0, 0);
  return Math.round((end - today) / 86400000);
};

const formatLastSeen = (isoDate) => {
  if (!isoDate) return 'لم يسجل الدخول بعد';
  return new Date(isoDate).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' });
};

const getStatusBadge = (company) => {
  const status = company.subscription_status;
  if (status === 'suspended') return { label: 'موقوف', className: 'bg-gray-100 text-gray-700 border-gray-300' };
  if (status === 'expired') return { label: 'منتهي', className: 'bg-red-50 text-red-700 border-red-200' };
  if (status === 'active') {
    const remaining = getRemainingDays(company.subscription_end_date);
    if (remaining !== null && remaining <= 7) {
      return { label: 'ينتهي قريباً', className: 'bg-orange-50 text-orange-700 border-orange-200' };
    }
    return { label: 'نشط', className: 'bg-green-50 text-green-700 border-green-200' };
  }
  return { label: status || '-', className: 'bg-gray-50 text-gray-700 border-gray-200' };
};

const SuperAdminCompanies = ({ onLogout, language, setLanguage }) => {
  const [companies, setCompanies] = useState([]);
  const [activePlans, setActivePlans] = useState([]);
  const [allPlans, setAllPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({
    name: '', owner_email: '', owner_name: '', owner_password: '', owner_phone: '', address: '', subscription_plan_id: ''
  });
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editForm, setEditForm] = useState({
    id: '', name: '', address: '', subscription_plan_id: '', originalPlanId: '',
    owner_name: '', owner_email: '', owner_phone: '', owner_password: '', owner_password_confirm: ''
  });
  const [editFieldErrors, setEditFieldErrors] = useState({});
  const [activateDialogOpen, setActivateDialogOpen] = useState(false);
  const [activateForm, setActivateForm] = useState({ id: '', subscription_start_date: '', subscription_end_date: '' });
  const [renewDialogOpen, setRenewDialogOpen] = useState(false);
  const [renewForm, setRenewForm] = useState({ id: '', subscription_plan_id: '' });

  useEffect(() => {
    fetch();
    // Poll silently (no loading spinner, no error toast) so company presence
    // (online/offline, last seen) updates without a page refresh.
    const interval = setInterval(() => fetch(true), 10000);
    return () => clearInterval(interval);
  }, []);

  const fetch = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [companiesRes, activePlansRes, allPlansRes] = await Promise.all([
        api.get('/admin/companies'),
        api.get('/owner/subscription-plans'),
        api.get('/admin/subscription-plans')
      ]);
      setCompanies(companiesRes.data);
      setActivePlans(activePlansRes.data);
      setAllPlans(allPlansRes.data);
    } catch (e) { if (!silent) toast.error('خطأ'); }
    if (!silent) setLoading(false);
  };

  const planLabel = (planId) => {
    const plan = allPlans.find((p) => p.id === planId);
    return plan ? `${plan.name} - ${formatCurrency(plan.price)}` : '-';
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.subscription_plan_id) {
      toast.error('يرجى اختيار خطة اشتراك');
      return;
    }
    try {
      await api.post('/admin/companies', form);
      toast.success('تمت إضافة الشركة');
      setDialogOpen(false);
      setForm({ name: '', owner_email: '', owner_name: '', owner_password: '', owner_phone: '', address: '', subscription_plan_id: '' });
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const openEdit = (company) => {
    setEditForm({
      id: company.id,
      name: company.name || '',
      address: company.address || '',
      subscription_plan_id: company.subscription_plan_id || '',
      originalPlanId: company.subscription_plan_id || '',
      owner_name: company.owner_name || '',
      owner_email: company.owner_email || '',
      owner_phone: company.owner_phone || '',
      owner_password: '',
      owner_password_confirm: ''
    });
    setEditFieldErrors({});
    setEditDialogOpen(true);
  };

  const handleEditSubmit = async (e) => {
    e.preventDefault();
    setEditFieldErrors({});

    if (editForm.owner_password || editForm.owner_password_confirm) {
      if (editForm.owner_password !== editForm.owner_password_confirm) {
        const msg = 'كلمتا المرور غير متطابقتين';
        setEditFieldErrors({ owner_password_confirm: msg });
        toast.error(msg);
        return;
      }
    }

    try {
      const payload = {
        name: editForm.name,
        address: editForm.address,
        owner_name: editForm.owner_name,
        owner_email: editForm.owner_email,
        owner_phone: editForm.owner_phone
      };
      // Only send subscription_plan_id (triggering the automatic price/max_employees
      // update) if the admin actually changed it - editing name/address shouldn't
      // force a plan re-selection.
      if (editForm.subscription_plan_id && editForm.subscription_plan_id !== editForm.originalPlanId) {
        payload.subscription_plan_id = editForm.subscription_plan_id;
      }
      if (editForm.owner_password || editForm.owner_password_confirm) {
        payload.owner_password = editForm.owner_password;
        payload.owner_password_confirm = editForm.owner_password_confirm;
      }
      await api.put(`/admin/companies/${editForm.id}`, payload);
      toast.success('تم التحديث');
      setEditDialogOpen(false);
      fetch();
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (detail && typeof detail === 'object' && detail.field) {
        setEditFieldErrors({ [detail.field]: detail.message });
        toast.error(detail.message);
      } else if (typeof detail === 'string') {
        toast.error(detail);
      } else {
        toast.error('حدث خطأ');
      }
    }
  };

  const openRenew = (company) => {
    setRenewForm({ id: company.id, subscription_plan_id: company.subscription_plan_id || '' });
    setRenewDialogOpen(true);
  };

  const handleRenewSubmit = async (e) => {
    e.preventDefault();
    if (!renewForm.subscription_plan_id) {
      toast.error('يرجى اختيار خطة اشتراك');
      return;
    }
    try {
      await api.post(`/admin/companies/${renewForm.id}/renew`, { subscription_plan_id: renewForm.subscription_plan_id });
      toast.success('تم تجديد الاشتراك بنجاح');
      setRenewDialogOpen(false);
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const openActivate = (company) => {
    setActivateForm({
      id: company.id,
      subscription_start_date: (company.subscription_start_date || '').slice(0, 10),
      subscription_end_date: (company.subscription_end_date || '').slice(0, 10)
    });
    setActivateDialogOpen(true);
  };

  const handleActivateSubmit = async (e) => {
    e.preventDefault();
    if (activateForm.subscription_end_date < activateForm.subscription_start_date) {
      toast.error('تاريخ الانتهاء لا يمكن أن يكون قبل تاريخ البدء');
      return;
    }
    try {
      await api.post(`/admin/companies/${activateForm.id}/activate`, {
        subscription_start_date: activateForm.subscription_start_date,
        subscription_end_date: activateForm.subscription_end_date
      });
      toast.success('تم تفعيل الاشتراك بنجاح');
      setActivateDialogOpen(false);
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const handleSuspend = async (id) => {
    try {
      await api.post(`/admin/companies/${id}/suspend`);
      toast.success('تم تعليق الاشتراك');
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
  };

  const handleReactivate = async (id) => {
    try {
      await api.post(`/admin/companies/${id}/reactivate`);
      toast.success('تم إعادة تفعيل الاشتراك');
      fetch();
    } catch (err) { toast.error(err.response?.data?.detail || 'حدث خطأ'); }
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
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الخطة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">سعر الاشتراك</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">تاريخ البدء</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">تاريخ الانتهاء</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الأيام المتبقية</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">حالة الشركة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">حالة المالك</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الموظفون المتصلون</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الإجراءات</th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((c) => {
                    const badge = getStatusBadge(c);
                    const remaining = getRemainingDays(c.subscription_end_date);
                    return (
                    <tr key={c.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`company-row-${c.id}`}>
                      <td className="px-6 py-4">
                        <p className="font-medium text-[#0A0A0A]">{c.name}</p>
                        <p className="text-xs text-gray-500">{c.address}</p>
                      </td>
                      <td className="px-6 py-4">{c.employee_count}{c.max_employees ? ` / ${c.max_employees}` : ''}</td>
                      <td className="px-6 py-4 text-gray-600" data-testid={`plan-name-${c.id}`}>{c.subscription_plan_id ? planLabel(c.subscription_plan_id) : '-'}</td>
                      <td className="px-6 py-4" data-testid={`subscription-price-${c.id}`}>{formatCurrency(c.subscription_price)}</td>
                      <td className="px-6 py-4">
                        <span data-testid={`status-badge-${c.id}`} className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${badge.className}`}>
                          {badge.label}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-gray-600">{(c.subscription_start_date || '').slice(0, 10) || '-'}</td>
                      <td className="px-6 py-4 text-gray-600">{(c.subscription_end_date || '').slice(0, 10) || '-'}</td>
                      <td className="px-6 py-4 text-gray-600">{c.subscription_status === 'active' && remaining !== null ? remaining : '-'}</td>
                      <td className="px-6 py-4" data-testid={`company-presence-${c.id}`}>
                        {c.company_online ? (
                          <div className="text-xs">
                            <span className="text-green-600 font-medium">🟢 Online</span>
                            <p className="text-gray-400">Active now</p>
                          </div>
                        ) : (
                          <div className="text-xs">
                            <span className="text-gray-500 font-medium">⚫ Offline</span>
                            <p className="text-gray-400">Last seen: {formatLastSeen(c.last_seen)}</p>
                          </div>
                        )}
                      </td>
                      <td className="px-6 py-4" data-testid={`owner-presence-${c.id}`}>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${c.owner_online ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-50 text-gray-500 border-gray-200'}`}>
                          {c.owner_online ? 'Online' : 'Offline'}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-gray-600" data-testid={`employees-online-${c.id}`}>
                        {c.employees_online ?? 0} / {c.employee_count} Online
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex gap-1 flex-wrap">
                          <Button variant="ghost" size="sm" onClick={() => openActivate(c)} title="تفعيل" data-testid={`activate-company-${c.id}`}>
                            <PlayCircle className="w-4 h-4 text-green-600" />
                          </Button>
                          {c.subscription_status === 'active' && (
                            <Button variant="ghost" size="sm" onClick={() => handleSuspend(c.id)} title="تعليق" data-testid={`suspend-company-${c.id}`}>
                              <PauseCircle className="w-4 h-4 text-gray-600" />
                            </Button>
                          )}
                          {c.subscription_status === 'suspended' && (
                            <Button variant="ghost" size="sm" onClick={() => handleReactivate(c.id)} title="إعادة تفعيل" data-testid={`reactivate-company-${c.id}`}>
                              <RotateCcw className="w-4 h-4 text-blue-600" />
                            </Button>
                          )}
                          <Button variant="ghost" size="sm" onClick={() => openRenew(c)} title="تجديد" data-testid={`renew-company-${c.id}`}>
                            <RefreshCw className="w-4 h-4 text-purple-600" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => openEdit(c)} data-testid={`edit-company-${c.id}`}>
                            <Pencil className="w-4 h-4" />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-600 hover:bg-red-50" data-testid={`delete-company-${c.id}`}>
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );})}
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
              <div>
                <Label>خطة الاشتراك</Label>
                <PlanCombobox plans={activePlans} value={form.subscription_plan_id} onChange={(id) => setForm({ ...form, subscription_plan_id: id })} testId="c-plan" />
                {form.subscription_plan_id && (() => {
                  const selected = activePlans.find((p) => p.id === form.subscription_plan_id);
                  return selected ? (
                    <p className="text-xs text-gray-500 mt-1">السعر: {formatCurrency(selected.price)} - الحد الأقصى للموظفين: {selected.max_employees}</p>
                  ) : null;
                })()}
              </div>
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

        <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
          <DialogContent className="max-w-md" data-testid="edit-company-dialog">
            <DialogHeader><DialogTitle>تعديل بيانات الشركة</DialogTitle></DialogHeader>
            <form onSubmit={handleEditSubmit} className="space-y-3">
              <div><Label>اسم الشركة</Label><Input data-testid="ec-name" required value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} /></div>
              <div><Label>العنوان</Label><Input data-testid="ec-address" value={editForm.address} onChange={(e) => setEditForm({ ...editForm, address: e.target.value })} /></div>
              <div>
                <Label>خطة الاشتراك</Label>
                <PlanCombobox plans={activePlans} value={editForm.subscription_plan_id} onChange={(id) => setEditForm({ ...editForm, subscription_plan_id: id })} testId="ec-plan" />
                {editForm.subscription_plan_id && (() => {
                  const selected = activePlans.find((p) => p.id === editForm.subscription_plan_id) || allPlans.find((p) => p.id === editForm.subscription_plan_id);
                  return selected ? (
                    <p className="text-xs text-gray-500 mt-1">السعر: {formatCurrency(selected.price)} - الحد الأقصى للموظفين: {selected.max_employees}</p>
                  ) : null;
                })()}
              </div>

              <div className="pt-2 border-t"><p className="text-xs text-gray-500 uppercase tracking-wider mb-2">حساب المالك</p></div>
              <div>
                <Label>الاسم الكامل للمالك</Label>
                <Input data-testid="ec-owner-name" required value={editForm.owner_name} onChange={(e) => setEditForm({ ...editForm, owner_name: e.target.value })} />
                {editFieldErrors.owner_name && <p className="text-xs text-red-600 mt-1" data-testid="ec-owner-name-error">{editFieldErrors.owner_name}</p>}
              </div>
              <div>
                <Label>البريد الإلكتروني للمالك</Label>
                <Input data-testid="ec-owner-email" type="email" required value={editForm.owner_email} onChange={(e) => setEditForm({ ...editForm, owner_email: e.target.value })} />
                {editFieldErrors.owner_email && <p className="text-xs text-red-600 mt-1" data-testid="ec-owner-email-error">{editFieldErrors.owner_email}</p>}
              </div>
              <div>
                <Label>هاتف المالك</Label>
                <Input data-testid="ec-owner-phone" required value={editForm.owner_phone} onChange={(e) => setEditForm({ ...editForm, owner_phone: e.target.value })} />
                {editFieldErrors.owner_phone && <p className="text-xs text-red-600 mt-1" data-testid="ec-owner-phone-error">{editFieldErrors.owner_phone}</p>}
              </div>

              <div className="pt-2 border-t"><p className="text-xs text-gray-500 uppercase tracking-wider mb-2">كلمة مرور جديدة (اختياري)</p></div>
              <div>
                <Label>كلمة المرور الجديدة</Label>
                <Input data-testid="ec-owner-password" type="password" placeholder="اتركها فارغة للإبقاء على كلمة المرور الحالية" value={editForm.owner_password} onChange={(e) => setEditForm({ ...editForm, owner_password: e.target.value })} />
              </div>
              <div>
                <Label>تأكيد كلمة المرور الجديدة</Label>
                <Input data-testid="ec-owner-password-confirm" type="password" value={editForm.owner_password_confirm} onChange={(e) => setEditForm({ ...editForm, owner_password_confirm: e.target.value })} />
                {editFieldErrors.owner_password_confirm && <p className="text-xs text-red-600 mt-1" data-testid="ec-owner-password-confirm-error">{editFieldErrors.owner_password_confirm}</p>}
              </div>

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-edit-company-btn">حفظ</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog open={activateDialogOpen} onOpenChange={setActivateDialogOpen}>
          <DialogContent className="max-w-md" data-testid="activate-dialog">
            <DialogHeader><DialogTitle>تفعيل الاشتراك</DialogTitle></DialogHeader>
            <form onSubmit={handleActivateSubmit} className="space-y-3">
              <div>
                <Label>تاريخ البدء</Label>
                <Input data-testid="activate-start-date" type="date" required value={activateForm.subscription_start_date} onChange={(e) => setActivateForm({ ...activateForm, subscription_start_date: e.target.value })} />
              </div>
              <div>
                <Label>تاريخ الانتهاء</Label>
                <Input data-testid="activate-end-date" type="date" required value={activateForm.subscription_end_date} onChange={(e) => setActivateForm({ ...activateForm, subscription_end_date: e.target.value })} />
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setActivateDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-activate-btn">تفعيل</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog open={renewDialogOpen} onOpenChange={setRenewDialogOpen}>
          <DialogContent className="max-w-md" data-testid="renew-dialog">
            <DialogHeader><DialogTitle>تجديد الاشتراك</DialogTitle></DialogHeader>
            <form onSubmit={handleRenewSubmit} className="space-y-3">
              <p className="text-sm text-gray-600">
                جدد باستخدام الخطة الحالية، أو اختر خطة أخرى قبل التجديد.
              </p>
              <div>
                <Label>خطة الاشتراك</Label>
                <PlanCombobox plans={activePlans} value={renewForm.subscription_plan_id} onChange={(id) => setRenewForm({ ...renewForm, subscription_plan_id: id })} testId="renew-plan" />
                {renewForm.subscription_plan_id && (() => {
                  const selected = activePlans.find((p) => p.id === renewForm.subscription_plan_id) || allPlans.find((p) => p.id === renewForm.subscription_plan_id);
                  return selected ? (
                    <p className="text-xs text-gray-500 mt-1">
                      السعر: {formatCurrency(selected.price)} - الحد الأقصى للموظفين: {selected.max_employees} - المدة: {selected.duration_months} شهر
                    </p>
                  ) : null;
                })()}
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setRenewDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-renew-btn">تجديد</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default SuperAdminCompanies;
