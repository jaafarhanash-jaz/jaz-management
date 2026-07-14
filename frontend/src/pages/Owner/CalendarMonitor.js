import { useEffect, useState, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import api from '@/utils/api';
import { Clock, Users } from 'lucide-react';
import { toast } from 'sonner';

const CATEGORY_LABELS = {
  meeting: 'اجتماع', company_holiday: 'عطلة رسمية', training: 'تدريب', task_deadline: 'موعد نهائي لمهمة',
  maintenance: 'صيانة', business_trip: 'رحلة عمل', reminder: 'تذكير', announcement: 'إعلان',
  personal: 'شخصي', other: 'أخرى',
};
const RESPONSE_LABELS = { no_response: 'لم يُجب', accepted: 'مقبول', declined: 'مرفوض', tentative: 'مبدئي' };
const RESPONSE_COLORS = { no_response: 'text-gray-400', accepted: 'text-green-600', declined: 'text-red-600', tentative: 'text-amber-600' };
const STATUS_LABELS = { scheduled: 'مجدول', in_progress: 'جارٍ الآن', completed: 'مكتمل', expired: 'منتهي', cancelled: 'ملغى' };
const EVENT_LABELS = {
  created: 'أُنشئ', owner_created: 'أُنشئ (بواسطة موظف)', updated: 'تحديث', time_changed: 'تغيير الوقت',
  location_changed: 'تغيير المكان', cancelled: 'إلغاء', attachment_added: 'إضافة مرفق',
  accepted: 'قبول', declined: 'رفض', tentative: 'رد مبدئي',
  participant_added: 'إضافة مشارك', participant_removed: 'إزالة مشارك',
};

const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' }) : '-';
const selectClass = 'h-9 rounded-md border border-input bg-white px-3 text-sm';

const CalendarMonitor = ({ onLogout, language, setLanguage }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const [filters, setFilters] = useState({ title: '', category: '', date_from: '', date_to: '' });
  const [activityOpen, setActivityOpen] = useState(false);
  const [activity, setActivity] = useState(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/owner/calendar/monitor?page=${page}&page_size=${pageSize}`);
      let filtered = res.data.items;
      if (filters.title) filtered = filtered.filter((e) => e.title.includes(filters.title));
      if (filters.category) filtered = filtered.filter((e) => e.category === filters.category);
      if (filters.date_from) filtered = filtered.filter((e) => e.start_date >= filters.date_from);
      if (filters.date_to) filtered = filtered.filter((e) => e.start_date <= filters.date_to);
      setItems(filtered);
      setTotal(res.data.total);
    } catch (e) {
      toast.error('خطأ في جلب البيانات');
    }
    setLoading(false);
  }, [page, filters]);

  useEffect(() => { fetchList(); }, [fetchList]);

  const openActivity = async (eventId) => {
    try {
      const res = await api.get(`/owner/calendar/events/${eventId}/activity`);
      setActivity(res.data);
      setActivityOpen(true);
    } catch (e) {
      toast.error('تعذر جلب السجل');
    }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="calendar-monitor-title">مراقبة التقويم</h1>
          <p className="text-gray-600 mt-2">رؤية كاملة لجميع مواعيد الشركة وردود المشاركين</p>
        </div>

        <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Input placeholder="العنوان" value={filters.title} data-testid="monitor-filter-title"
              onChange={(e) => setFilters({ ...filters, title: e.target.value })} />
            <select className={selectClass} value={filters.category} data-testid="monitor-filter-category"
              onChange={(e) => setFilters({ ...filters, category: e.target.value })}>
              <option value="">كل التصنيفات</option>
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <Input type="date" value={filters.date_from} data-testid="monitor-filter-date-from"
              onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
            <Input type="date" value={filters.date_to} data-testid="monitor-filter-date-to"
              onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
          </div>
        </Card>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : items.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <p className="text-gray-500">لا توجد مواعيد مطابقة</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الرقم</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">العنوان</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">التصنيف</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">التاريخ</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">أنشأه</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">ردود المشاركين</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">السجل</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((ev) => (
                    <tr key={ev.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`monitor-row-${ev.id}`}>
                      <td className="px-4 py-3 font-mono text-xs">{ev.reference_number}</td>
                      <td className="px-4 py-3 font-medium text-[#0A0A0A]">{ev.title}</td>
                      <td className="px-4 py-3 text-xs text-gray-600">{CATEGORY_LABELS[ev.category]}</td>
                      <td className="px-4 py-3 text-xs text-gray-500">{ev.start_date} {ev.start_time || ''}</td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded-full text-xs border bg-gray-50 text-gray-700 border-gray-200">
                          {STATUS_LABELS[ev.display_status]}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-600">{ev.created_by_name}</td>
                      <td className="px-4 py-3">
                        {ev.participants?.length > 0 ? (
                          <div className="flex items-center gap-1 text-xs" data-testid={`monitor-responses-${ev.id}`}>
                            <Users className="w-3.5 h-3.5 text-gray-400" />
                            {ev.participants.map((p) => (
                              <span key={p.id} className={RESPONSE_COLORS[p.attendance_status]} title={p.participant_name}>●</span>
                            ))}
                            <span className="text-gray-400">
                              ({ev.participants.filter((p) => p.attendance_status !== 'no_response').length}/{ev.participants.length})
                            </span>
                          </div>
                        ) : <span className="text-gray-400 text-xs">-</span>}
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => openActivity(ev.id)} className="text-[#0033A0] hover:underline text-xs flex items-center gap-1" data-testid={`monitor-activity-btn-${ev.id}`}>
                          <Clock className="w-3.5 h-3.5" /> عرض
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {total > pageSize && (
          <div className="flex justify-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>السابق</Button>
            <span className="text-sm text-gray-500 self-center">صفحة {page} من {Math.ceil(total / pageSize)}</span>
            <Button variant="outline" size="sm" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>التالي</Button>
          </div>
        )}

        <Dialog open={activityOpen} onOpenChange={setActivityOpen}>
          <DialogContent className="max-w-md max-h-[70vh] overflow-y-auto" data-testid="monitor-activity-dialog">
            <DialogHeader><DialogTitle>سجل النشاط</DialogTitle></DialogHeader>
            {activity?.length === 0 ? (
              <p className="text-center text-gray-500 py-6">لا يوجد نشاط مسجل</p>
            ) : (
              <ol className="relative border-e-2 border-gray-200 me-2 space-y-4 py-2">
                {activity?.map((a) => (
                  <li key={a.id} className="ms-4">
                    <span className="absolute w-2.5 h-2.5 bg-[#0033A0] rounded-full mt-1.5 -start-[5px] border border-white" />
                    <p className="text-xs text-gray-400">{formatDateTime(a.created_at)}</p>
                    <p className="text-sm font-medium text-[#0A0A0A]">{EVENT_LABELS[a.verb] || a.verb} - {a.actor_name}</p>
                  </li>
                ))}
              </ol>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default CalendarMonitor;
