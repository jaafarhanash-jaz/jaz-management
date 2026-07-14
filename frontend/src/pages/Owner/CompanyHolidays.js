import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import api from '@/utils/api';
import { Plus, CalendarOff, Pencil, Ban, Power, CalendarDays } from 'lucide-react';
import { toast } from 'sonner';

// Sunday=0..Saturday=6 - same convention as working_hours.working_days.
// Date.getUTCDay() already returns this exact indexing, so no conversion is
// needed anywhere below.
const WEEKDAY_LABELS = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];
const weekdayOf = (dateStr) => new Date(`${dateStr}T00:00:00Z`).getUTCDay();

const emptyForm = { title: '', description: '', start_date: '', end_date: '', annual_recurring: false };
const defaultWorkingHours = { working_days: [0, 1, 2, 3, 4], start_time: '09:00', end_time: '18:00' };

const OwnerCompanyHolidays = ({ onLogout, language, setLanguage }) => {
  const [holidays, setHolidays] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [submitting, setSubmitting] = useState(false);

  // Default Weekly Working Schedule - a dedicated UI over the existing
  // working_hours settings (GET/PUT /owner/calendar/working-hours), not a
  // new collection. Attendance/Reports already read this same setting
  // (see is_weekly_holiday on the backend).
  const [workingHours, setWorkingHours] = useState(defaultWorkingHours);
  const [scheduleLoading, setScheduleLoading] = useState(true);
  const [savingSchedule, setSavingSchedule] = useState(false);

  useEffect(() => { fetchHolidays(); fetchWorkingHours(); }, []);

  const fetchHolidays = async () => {
    setLoading(true);
    try {
      const res = await api.get('/calendar/holidays');
      setHolidays(res.data);
    } catch (e) {
      toast.error('خطأ في جلب العطلات');
    }
    setLoading(false);
  };

  const fetchWorkingHours = async () => {
    setScheduleLoading(true);
    try {
      const res = await api.get('/owner/calendar/working-hours');
      setWorkingHours(res.data);
    } catch (e) {
      toast.error('خطأ في جلب الجدول الأسبوعي');
    }
    setScheduleLoading(false);
  };

  const toggleScheduleDay = (wd) => {
    setWorkingHours((wh) => ({
      ...wh,
      working_days: wh.working_days.includes(wd)
        ? wh.working_days.filter((d) => d !== wd)
        : [...wh.working_days, wd].sort((a, b) => a - b),
    }));
  };

  const saveSchedule = async () => {
    setSavingSchedule(true);
    try {
      const res = await api.put('/owner/calendar/working-hours', workingHours);
      setWorkingHours(res.data.working_hours);
      toast.success('تم حفظ الجدول الأسبوعي');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
    setSavingSchedule(false);
  };

  const openAdd = () => {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };

  const openEdit = (h) => {
    setEditing(h);
    setForm({
      title: h.title, description: h.description || '',
      start_date: h.start_date, end_date: h.end_date,
      annual_recurring: h.recurrence_type === 'yearly',
    });
    setDialogOpen(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.start_date || !form.end_date) {
      toast.error('يرجى تحديد تاريخ البداية والنهاية');
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await api.patch(`/calendar/events/${editing.id}`, {
          title: form.title, description: form.description,
          start_date: form.start_date, end_date: form.end_date,
          override_conflicts: true,
        });
        toast.success('تم تحديث العطلة');
      } else {
        await api.post('/calendar/events', {
          title: form.title, description: form.description,
          category: 'company_holiday',
          start_date: form.start_date, end_date: form.end_date, all_day: true,
          visibility: 'company', recipient_type: 'company',
          recurrence_type: form.annual_recurring ? 'yearly' : 'none',
          recurrence_end_type: 'never',
          override_conflicts: true,
        });
        toast.success('تمت إضافة العطلة');
      }
      setDialogOpen(false);
      fetchHolidays();
    } catch (err) {
      toast.error(err.response?.data?.detail?.message || err.response?.data?.detail || 'حدث خطأ');
    }
    setSubmitting(false);
  };

  const toggleActive = async (h) => {
    try {
      await api.post(`/calendar/events/${h.id}/${h.is_active ? 'deactivate' : 'reactivate'}`);
      toast.success(h.is_active ? 'تم تعطيل العطلة' : 'تم تفعيل العطلة');
      fetchHolidays();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
  };

  const cancelHoliday = async (h) => {
    if (!window.confirm(`هل تريد حذف العطلة "${h.title}"؟`)) return;
    try {
      await api.post(`/calendar/events/${h.id}/cancel`, null, { params: { scope: 'entire_series' } });
      toast.success('تم حذف العطلة');
      fetchHolidays();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
  };

  const recurrenceLabel = (h) => {
    if (h.recurrence_type === 'weekly') return `متكررة أسبوعياً - كل يوم ${WEEKDAY_LABELS[weekdayOf(h.start_date)]}`;
    if (h.recurrence_type === 'yearly') return 'متكررة سنوياً';
    return h.start_date === h.end_date ? h.start_date : `${h.start_date} → ${h.end_date}`;
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-8">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="company-holidays-title">عطلات الشركة</h1>
          <p className="text-gray-600 mt-2">إدارة العطلات الرسمية والجدول الأسبوعي الافتراضي للشركة</p>
        </div>

        {/* Section 1: Official Company Holidays */}
        <div className="space-y-4">
          <div className="flex justify-between items-start flex-wrap gap-4">
            <h2 className="text-2xl font-bold text-[#0A0A0A] flex items-center gap-2">
              <CalendarOff className="w-5 h-5 text-[#0033A0]" /> العطلات الرسمية للشركة
            </h2>
            <Button onClick={openAdd} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="add-holiday-btn">
              <Plus className="w-4 h-4 me-2" /> إضافة عطلة
            </Button>
          </div>

          {loading ? (
            <div className="text-center py-12 text-gray-500">Loading...</div>
          ) : holidays.length === 0 ? (
            <Card className="p-12 text-center bg-white border border-gray-200">
              <CalendarOff className="w-12 h-12 mx-auto text-gray-300 mb-4" />
              <p className="text-gray-500">لا توجد عطلات مضافة حالياً</p>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {holidays.map((h) => {
                const cancelled = h.status === 'cancelled';
                return (
                  <Card key={h.id} className={`p-6 bg-white border rounded-md ${cancelled ? 'border-gray-200 opacity-60' : 'border-gray-200'}`} data-testid={`holiday-card-${h.id}`}>
                    <div className="flex items-start justify-between mb-2">
                      <h3 className="text-lg font-medium text-[#0A0A0A]">{h.title}</h3>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${
                        cancelled ? 'bg-gray-100 text-gray-500 border-gray-300' :
                        h.is_active ? 'bg-green-50 text-green-700 border-green-200' : 'bg-yellow-50 text-yellow-700 border-yellow-200'
                      }`}>
                        {cancelled ? 'محذوفة' : h.is_active ? 'مفعّلة' : 'معطّلة'}
                      </span>
                    </div>
                    {h.description && <p className="text-sm text-gray-600 mb-2">{h.description}</p>}
                    <p className="text-xs text-gray-500 mb-1">{recurrenceLabel(h)}</p>
                    <p className="text-xs text-gray-400 mb-4">أنشأها: {h.created_by_name || '-'}</p>
                    {!cancelled && (
                      <div className="flex items-center gap-2">
                        <button onClick={() => openEdit(h)} className="text-gray-500 hover:text-[#0033A0]" title="تعديل" data-testid={`edit-holiday-${h.id}`}>
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button onClick={() => toggleActive(h)} className={h.is_active ? 'text-yellow-600 hover:text-yellow-700' : 'text-green-600 hover:text-green-700'} title={h.is_active ? 'تعطيل' : 'تفعيل'} data-testid={`toggle-holiday-${h.id}`}>
                          <Power className="w-4 h-4" />
                        </button>
                        <button onClick={() => cancelHoliday(h)} className="text-red-600 hover:text-red-700" title="حذف" data-testid={`delete-holiday-${h.id}`}>
                          <Ban className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
        </div>

        {/* Section 2: Default Weekly Working Schedule */}
        <div className="space-y-4">
          <h2 className="text-2xl font-bold text-[#0A0A0A] flex items-center gap-2">
            <CalendarDays className="w-5 h-5 text-[#0033A0]" /> الجدول الأسبوعي الافتراضي
          </h2>
          <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="weekly-schedule-section">
            <p className="text-sm text-gray-500 mb-4">
              حدد أيام العمل الرسمية للشركة. الأيام المحددة كعطلة أسبوعية لن تُحتسب كغياب أو تأخير أو انصراف مبكر على أي موظف.
            </p>
            {scheduleLoading ? (
              <div className="text-center py-6 text-gray-500">Loading...</div>
            ) : (
              <>
                <div className="divide-y divide-gray-100 border border-gray-200 rounded-md">
                  {WEEKDAY_LABELS.map((label, idx) => {
                    const isWorking = workingHours.working_days.includes(idx);
                    return (
                      <div key={idx} className="flex items-center justify-between px-4 py-3">
                        <span className="font-medium text-[#0A0A0A]">{label}</span>
                        <div className="flex items-center gap-3">
                          <span className={`text-xs font-medium ${isWorking ? 'text-emerald-700' : 'text-red-600'}`}>
                            {isWorking ? 'يوم عمل' : 'عطلة أسبوعية'}
                          </span>
                          <Switch
                            checked={isWorking}
                            data-testid={`schedule-day-${idx}`}
                            onCheckedChange={() => toggleScheduleDay(idx)}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <Button
                  onClick={saveSchedule}
                  disabled={savingSchedule}
                  className="mt-4 bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm"
                  data-testid="save-schedule-btn"
                >
                  {savingSchedule ? 'جاري الحفظ...' : 'حفظ الجدول الأسبوعي'}
                </Button>
              </>
            )}
          </Card>
        </div>

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-md" data-testid="holiday-dialog">
            <DialogHeader><DialogTitle>{editing ? 'تعديل عطلة' : 'إضافة عطلة جديدة'}</DialogTitle></DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div><Label>العنوان</Label><Input required data-testid="holiday-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
              <div><Label>الوصف</Label><Textarea rows={2} data-testid="holiday-description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>تاريخ البداية</Label><Input type="date" required data-testid="holiday-start-date" value={form.start_date} onChange={(e) => { const value = e.target.value; setForm((f) => ({ ...f, start_date: value, end_date: f.end_date || value })); }} /></div>
                <div><Label>تاريخ النهاية</Label><Input type="date" required data-testid="holiday-end-date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></div>
              </div>
              {!editing && (
                <div className="flex items-center gap-2">
                  <Checkbox id="annual-recurring" data-testid="holiday-annual-recurring" checked={form.annual_recurring} onCheckedChange={(v) => setForm({ ...form, annual_recurring: !!v })} />
                  <Label htmlFor="annual-recurring" className="cursor-pointer">تتكرر سنوياً بنفس التاريخ</Label>
                </div>
              )}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>إلغاء</Button>
                <Button type="submit" disabled={submitting} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-holiday-btn">
                  {submitting ? 'جاري الحفظ...' : 'حفظ'}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default OwnerCompanyHolidays;
