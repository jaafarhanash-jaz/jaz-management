import { useEffect, useState, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { Pin, Search, Paperclip, Reply as ReplyIcon, Clock } from 'lucide-react';
import { toast } from 'sonner';

const PRIORITY_LABELS = { normal: 'عادي', important: 'مهم', urgent: 'عاجل' };
const PRIORITY_COLORS = {
  normal: 'bg-gray-50 text-gray-700 border-gray-200',
  important: 'bg-amber-50 text-amber-700 border-amber-200',
  urgent: 'bg-red-50 text-red-700 border-red-200',
};
const STATUS_OPTIONS = [
  { value: 'all', label: 'الكل' },
  { value: 'delivered', label: 'تم الإرسال' },
  { value: 'seen', label: 'تمت المشاهدة' },
  { value: 'accepted', label: 'مقبولة' },
  { value: 'completed', label: 'مكتملة' },
  { value: 'archived', label: 'مؤرشفة' },
  { value: 'closed', label: 'مغلقة' },
];
const ATTACHMENT_TYPE_OPTIONS = ['all', 'image', 'pdf', 'word', 'excel', 'powerpoint', 'zip', 'audio', 'video', 'other'];
const EVENT_LABELS = { sent: 'أُرسلت', delivered: 'وصلت', opened: 'شوهدت', accepted: 'قُبلت', completed: 'أُنجزت', archived: 'أُرشفت', closed: 'أُغلقت', replied: 'رد جديد' };

const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' }) : '-';

const selectClass = 'h-9 rounded-md border border-input bg-white px-3 text-sm';

const CommunicationCenter = ({ onLogout, language, setLanguage }) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const [filters, setFilters] = useState({
    reference_number: '', subject: '', sender: '', recipient: '', department: '',
    tags: '', priority: '', status: '', attachment_type: '', date_from: '', date_to: '',
  });

  const [timelineOpen, setTimelineOpen] = useState(false);
  const [timeline, setTimeline] = useState(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v); });
      const res = await api.get(`/owner/messages/communication-center?${params.toString()}`);
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch (e) {
      toast.error('خطأ في جلب البيانات');
    }
    setLoading(false);
  }, [page, filters]);

  useEffect(() => { fetchList(); }, [fetchList]);

  const applyFilters = () => { setPage(1); fetchList(); };

  const togglePin = async (id) => {
    try {
      await api.post(`/owner/messages/${id}/pin`);
      fetchList();
    } catch (e) {
      toast.error('حدث خطأ');
    }
  };

  const openTimeline = async (id) => {
    try {
      const res = await api.get(`/owner/messages/${id}/timeline`);
      setTimeline(res.data);
      setTimelineOpen(true);
    } catch (e) {
      toast.error('تعذر جلب السجل الزمني');
    }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="communication-center-title">مركز التواصل</h1>
          <p className="text-gray-600 mt-2">رؤية كاملة لجميع رسائل العمل داخل الشركة</p>
        </div>

        <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الرقم المرجعي</label>
              <Input data-testid="cc-filter-reference" value={filters.reference_number}
                onChange={(e) => setFilters({ ...filters, reference_number: e.target.value })} placeholder="MSG-000001" />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">العنوان</label>
              <Input data-testid="cc-filter-subject" value={filters.subject}
                onChange={(e) => setFilters({ ...filters, subject: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">المرسل</label>
              <Input data-testid="cc-filter-sender" value={filters.sender}
                onChange={(e) => setFilters({ ...filters, sender: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">المستلم</label>
              <Input data-testid="cc-filter-recipient" value={filters.recipient}
                onChange={(e) => setFilters({ ...filters, recipient: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">القسم</label>
              <Input data-testid="cc-filter-department" value={filters.department}
                onChange={(e) => setFilters({ ...filters, department: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الوسوم</label>
              <Input data-testid="cc-filter-tags" value={filters.tags} placeholder="Sales,Report"
                onChange={(e) => setFilters({ ...filters, tags: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الأولوية</label>
              <select className={`${selectClass} w-full`} value={filters.priority} data-testid="cc-filter-priority"
                onChange={(e) => setFilters({ ...filters, priority: e.target.value })}>
                <option value="">الكل</option>
                <option value="normal">عادي</option>
                <option value="important">مهم</option>
                <option value="urgent">عاجل</option>
              </select>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الحالة</label>
              <select className={`${selectClass} w-full`} value={filters.status} data-testid="cc-filter-status"
                onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                {STATUS_OPTIONS.map((s) => <option key={s.value} value={s.value === 'all' ? '' : s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">نوع المرفق</label>
              <select className={`${selectClass} w-full`} value={filters.attachment_type} data-testid="cc-filter-attachment-type"
                onChange={(e) => setFilters({ ...filters, attachment_type: e.target.value })}>
                {ATTACHMENT_TYPE_OPTIONS.map((a) => <option key={a} value={a === 'all' ? '' : a}>{a === 'all' ? 'الكل' : a}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">من تاريخ</label>
              <Input type="date" value={filters.date_from} data-testid="cc-filter-date-from"
                onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">إلى تاريخ</label>
              <Input type="date" value={filters.date_to} data-testid="cc-filter-date-to"
                onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
            </div>
          </div>
          <Button onClick={applyFilters} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="cc-apply-filters">
            <Search className="w-4 h-4 me-2" /> بحث
          </Button>
        </Card>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : items.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <p className="text-gray-500">لا توجد رسائل مطابقة</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الرقم</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">المرسل</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">المستلمون</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">القسم</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الأولوية</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">السرية</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">العنوان</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">التقدم</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الردود</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">تاريخ الإرسال</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">السجل الزمني</th>
                    <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">تثبيت</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((m) => (
                    <tr key={m.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`cc-row-${m.id}`}>
                      <td className="px-4 py-3 font-mono text-xs">{m.reference_number}</td>
                      <td className="px-4 py-3 font-medium text-[#0A0A0A]">{m.sender_name}</td>
                      <td className="px-4 py-3 text-gray-600 text-xs">{(m.recipient_names || []).join('، ') || '-'}</td>
                      <td className="px-4 py-3 text-gray-600 text-xs">{m.recipient_department || '-'}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs border ${PRIORITY_COLORS[m.priority]}`}>{PRIORITY_LABELS[m.priority]}</span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{m.confidentiality}</td>
                      <td className="px-4 py-3 max-w-[200px] truncate">
                        {m.subject}
                        {m.attachment_count > 0 && <Paperclip className="w-3 h-3 inline ms-1 text-gray-400" />}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-600">
                        {m.delivery_progress ? `${m.delivery_progress.seen}/${m.delivery_progress.total} شوهدت` : '-'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-600">
                        {m.reply_count > 0 && <span><ReplyIcon className="w-3 h-3 inline me-1" />{m.reply_count}</span>}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{formatDateTime(m.created_at)}</td>
                      <td className="px-4 py-3">
                        <button onClick={() => openTimeline(m.id)} className="text-[#0033A0] hover:underline text-xs flex items-center gap-1" data-testid={`cc-timeline-btn-${m.id}`}>
                          <Clock className="w-3.5 h-3.5" /> عرض
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => togglePin(m.id)} data-testid={`cc-pin-btn-${m.id}`}>
                          <Pin className={`w-4 h-4 ${m.is_pinned ? 'text-[#0033A0] fill-[#0033A0]' : 'text-gray-300'}`} />
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
            <span className="text-sm text-gray-500 self-center">صفحة {page} من {Math.ceil(total / pageSize)} ({total} رسالة)</span>
            <Button variant="outline" size="sm" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>التالي</Button>
          </div>
        )}

        <Dialog open={timelineOpen} onOpenChange={setTimelineOpen}>
          <DialogContent className="max-w-md max-h-[80vh] overflow-y-auto" data-testid="cc-timeline-dialog">
            <DialogHeader><DialogTitle>السجل الزمني - {timeline?.reference_number}</DialogTitle></DialogHeader>
            {timeline && (
              <ol className="relative border-e-2 border-gray-200 me-2 space-y-5 py-2" data-testid="cc-timeline-events">
                {timeline.events.map((e, i) => (
                  <li key={i} className="ms-4">
                    <span className="absolute w-2.5 h-2.5 bg-[#0033A0] rounded-full mt-1.5 -start-[5px] border border-white" />
                    <p className="text-xs text-gray-400">{formatDateTime(e.timestamp)}</p>
                    <p className="text-sm font-medium text-[#0A0A0A]">{EVENT_LABELS[e.label] || e.label} - {e.actor_name}</p>
                  </li>
                ))}
                {timeline.events.length === 0 && <p className="text-sm text-gray-500 text-center py-6">لا توجد أحداث بعد</p>}
              </ol>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default CommunicationCenter;
