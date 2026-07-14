import { useEffect, useState, useCallback, useRef } from 'react';
import {
  format, startOfMonth, endOfMonth, startOfWeek, endOfWeek, eachDayOfInterval,
  addMonths, subMonths, addWeeks, subWeeks, addDays, subDays, isSameDay, isSameMonth, isToday, parseISO,
} from 'date-fns';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import {
  ChevronRight, ChevronLeft, CalendarDays, Search, Plus, MapPin, Video, Building2,
  Users, AlertTriangle, Paperclip, Bell, Repeat, X, Download, CheckCircle2, XCircle, HelpCircle,
} from 'lucide-react';
import { toast } from 'sonner';

const CATEGORY_LABELS = {
  meeting: 'اجتماع', company_holiday: 'عطلة رسمية', training: 'تدريب', task_deadline: 'موعد نهائي لمهمة',
  maintenance: 'صيانة', business_trip: 'رحلة عمل', reminder: 'تذكير', announcement: 'إعلان',
  personal: 'شخصي', other: 'أخرى',
};
const PRIORITY_LABELS = { low: 'منخفضة', normal: 'عادية', high: 'مرتفعة', critical: 'حرجة' };
const PRIORITY_ACCENT = { low: 'border-s-4 border-gray-300', normal: '', high: 'border-s-4 border-amber-500', critical: 'border-s-4 border-red-600' };
const VISIBILITY_LABELS = { private: 'خاص', department: 'القسم', company: 'الشركة', owner_only: 'المالك فقط' };
const LOCATION_TYPE_LABELS = { office: 'المكتب', client_site: 'موقع العميل', online: 'عبر الإنترنت', other: 'أخرى' };
const RECURRENCE_LABELS = { none: 'بدون تكرار', daily: 'يومي', weekly: 'أسبوعي', monthly: 'شهري', yearly: 'سنوي', custom: 'فترة مخصصة' };
const RESPONSE_LABELS = { no_response: 'لم يُجب', accepted: 'مقبول', declined: 'مرفوض', tentative: 'مبدئي' };
const REMINDER_PRESET_LABELS = { '5m': '5 دقائق', '15m': '15 دقيقة', '30m': '30 دقيقة', '1h': 'ساعة', '6h': '6 ساعات', '1d': 'يوم', '3d': '3 أيام', '1w': 'أسبوع' };

const emptyCompose = () => ({
  title: '', description: '', category: 'meeting', priority: 'normal', custom_color: '',
  start_date: format(new Date(), 'yyyy-MM-dd'), start_time: '09:00', end_date: format(new Date(), 'yyyy-MM-dd'), end_time: '10:00',
  all_day: false, location_type: '', location: '', online_link: '',
  visibility: 'company', recipient_type: 'employee', recipient_ids: [],
  recurrence_type: 'none', recurrence_interval: 1, recurrence_end_type: 'never', recurrence_end_value: '',
});

const selectClass = 'h-9 w-full rounded-md border border-input bg-white px-3 text-sm';

const CalendarPage = ({ onLogout, language, setLanguage, userRole }) => {
  const [view, setView] = useState('month');
  const [currentDate, setCurrentDate] = useState(new Date());
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [workingHours, setWorkingHours] = useState({ working_days: [0, 1, 2, 3, 4], start_time: '08:00', end_time: '17:00' });
  const [employees, setEmployees] = useState([]);
  const [jumpDate, setJumpDate] = useState('');

  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchResults, setSearchResults] = useState(null);

  const [composeOpen, setComposeOpen] = useState(false);
  const [compose, setCompose] = useState(emptyCompose());
  const [editingEvent, setEditingEvent] = useState(null);
  const [editScope, setEditScope] = useState('entire_series');
  const [conflictWarning, setConflictWarning] = useState(null);
  const [pendingReminders, setPendingReminders] = useState([]);
  const [saving, setSaving] = useState(false);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailEvent, setDetailEvent] = useState(null);
  const knownEventIds = useRef(null);

  const isOwner = userRole === 'company_owner';
  const departments = [...new Set(employees.map((e) => e.department).filter(Boolean))];

  useEffect(() => {
    api.get('/owner/employees').then((res) => setEmployees(res.data)).catch(() => {});
  }, []);

  const viewRange = useCallback(() => {
    if (view === 'month') return { start: startOfWeek(startOfMonth(currentDate)), end: endOfWeek(endOfMonth(currentDate)) };
    if (view === 'week') return { start: startOfWeek(currentDate), end: endOfWeek(currentDate) };
    if (view === 'day') return { start: currentDate, end: currentDate };
    return { start: currentDate, end: addDays(currentDate, 30) }; // agenda
  }, [view, currentDate]);

  const fetchEvents = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    const { start, end } = viewRange();
    try {
      const res = await api.get(`/calendar/events?view_start=${format(start, 'yyyy-MM-dd')}&view_end=${format(end, 'yyyy-MM-dd')}&page_size=500`);
      setEvents(res.data.items);
      setWorkingHours(res.data.working_hours);
      return res.data.items;
    } catch (e) {
      if (!silent) toast.error('خطأ في جلب الأحداث');
    } finally {
      if (!silent) setLoading(false);
    }
  }, [viewRange]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  // Arrival detection for new invites - same 10s poll-and-diff pattern used
  // by Work Messages' inbox and the legacy urgent-task alert, reused only as
  // a pattern (this file duplicates the tiny beep helper rather than import
  // from a do-not-modify file).
  useEffect(() => {
    const playBeep = () => {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const ctx = new AudioCtx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine'; osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start(); osc.stop(ctx.currentTime + 0.4);
      } catch (e) { /* ignore */ }
    };
    const poll = async () => {
      const today = format(new Date(), 'yyyy-MM-dd');
      try {
        const res = await api.get(`/calendar/events?view_start=${today}&view_end=${today}&page_size=100`);
        const ids = new Set(res.data.items.map((e) => e.id + e.occurrence_date));
        if (knownEventIds.current !== null) {
          const arrived = [...ids].filter((id) => !knownEventIds.current.has(id));
          if (arrived.length > 0) toast.info('📅 موعد جديد أُضيف اليوم', { duration: 5000 }), playBeep();
        }
        knownEventIds.current = ids;
      } catch (e) { /* ignore */ }
    };
    poll();
    const interval = setInterval(poll, 10000);
    return () => clearInterval(interval);
  }, []);

  const isOutsideWorkingHours = (ev) => {
    if (ev.all_day) return false;
    const day = new Date(ev.occurrence_start).getDay();
    if (!workingHours.working_days.includes(day)) return true;
    const t = ev.start_time;
    return t && (t < workingHours.start_time || t > workingHours.end_time);
  };

  // ---- Navigation ----
  const goToday = () => setCurrentDate(new Date());
  const goPrev = () => setCurrentDate((d) => view === 'month' ? subMonths(d, 1) : view === 'week' ? subWeeks(d, 1) : subDays(d, 1));
  const goNext = () => setCurrentDate((d) => view === 'month' ? addMonths(d, 1) : view === 'week' ? addWeeks(d, 1) : addDays(d, 1));
  const jumpToDate = () => { if (jumpDate) setCurrentDate(parseISO(jumpDate)); };

  // ---- Search ----
  const runSearch = async () => {
    try {
      const res = await api.get(`/calendar/events/search?title=${encodeURIComponent(searchQuery)}`);
      setSearchResults(res.data.items);
      setSearchOpen(true);
    } catch (e) {
      toast.error('خطأ في البحث');
    }
  };

  // ---- Compose ----
  const openCompose = (prefillDate = null) => {
    const form = emptyCompose();
    if (prefillDate) {
      form.start_date = format(prefillDate, 'yyyy-MM-dd');
      form.end_date = format(prefillDate, 'yyyy-MM-dd');
    }
    setCompose(form);
    setEditingEvent(null);
    setPendingReminders([]);
    setConflictWarning(null);
    setComposeOpen(true);
  };

  const openEditCompose = (ev) => {
    setCompose({
      title: ev.title, description: ev.description, category: ev.category, priority: ev.priority,
      custom_color: ev.custom_color || '', start_date: ev.start_date, start_time: ev.start_time || '',
      end_date: ev.end_date, end_time: ev.end_time || '', all_day: ev.all_day,
      location_type: ev.location_type || '', location: ev.location || '', online_link: ev.online_link || '',
      visibility: ev.visibility, recipient_type: ev.recipient_type, recipient_ids: ev.recipient_ids || [],
      recurrence_type: ev.recurrence_type, recurrence_interval: ev.recurrence_interval,
      recurrence_end_type: ev.recurrence_end_type, recurrence_end_value: ev.recurrence_end_value || '',
    });
    setEditingEvent(ev);
    setEditScope(ev.recurrence_type !== 'none' ? 'this_event_only' : 'entire_series');
    setPendingReminders([]);
    setConflictWarning(null);
    setComposeOpen(true);
    setDetailOpen(false);
  };

  const toggleRecipient = (id) => {
    setCompose((c) => ({ ...c, recipient_ids: c.recipient_ids.includes(id) ? c.recipient_ids.filter((x) => x !== id) : [...c.recipient_ids, id] }));
  };

  const buildPayload = (override = false) => ({
    title: compose.title, description: compose.description, category: compose.category, priority: compose.priority,
    custom_color: compose.custom_color || null,
    start_date: compose.start_date, start_time: compose.all_day ? null : compose.start_time,
    end_date: compose.end_date, end_time: compose.all_day ? null : compose.end_time, all_day: compose.all_day,
    location_type: compose.location_type || null, location: compose.location || null, online_link: compose.online_link || null,
    visibility: compose.visibility, recipient_type: compose.recipient_type, recipient_ids: compose.recipient_ids,
    recurrence_type: compose.recurrence_type, recurrence_interval: Number(compose.recurrence_interval) || 1,
    recurrence_end_type: compose.recurrence_end_type, recurrence_end_value: compose.recurrence_end_value || null,
    override_conflicts: override,
  });

  const submitCompose = async (override = false) => {
    if (!compose.title.trim()) { toast.error('العنوان مطلوب'); return; }
    if (compose.recipient_type !== 'owner' && compose.recipient_type !== 'company' && compose.recipient_ids.length === 0) {
      toast.error('يرجى اختيار مستلم واحد على الأقل');
      return;
    }
    setSaving(true);
    try {
      let saved;
      if (editingEvent) {
        const params = new URLSearchParams({ scope: editScope });
        if (editScope !== 'entire_series') params.set('occurrence_date', editingEvent.occurrence_date);
        saved = (await api.patch(`/calendar/events/${editingEvent.id}?${params.toString()}`, buildPayload(override))).data;
      } else {
        saved = (await api.post('/calendar/events', buildPayload(override))).data;
      }
      for (const reminder of pendingReminders) {
        await api.post(`/calendar/events/${saved.id}/reminders`, reminder);
      }
      toast.success(editingEvent ? 'تم تحديث الموعد' : 'تم إنشاء الموعد');
      setComposeOpen(false);
      setConflictWarning(null);
      fetchEvents(true);
    } catch (e) {
      if (e.response?.status === 409) {
        setConflictWarning(e.response.data.detail);
      } else {
        toast.error(e.response?.data?.detail || 'حدث خطأ');
      }
    }
    setSaving(false);
  };

  const addPendingReminder = (preset) => {
    if (pendingReminders.some((r) => r.preset === preset)) return;
    setPendingReminders((prev) => [...prev, { preset }]);
  };

  // ---- Event detail / actions ----
  const openDetail = async (ev) => {
    try {
      const res = await api.get(`/calendar/events/${ev.id}`);
      setDetailEvent({ ...res.data, occurrence_date: ev.occurrence_date });
      setNotesForm(res.data.meeting_notes || { summary: '', decisions: '', action_items: '' });
      setDetailOpen(true);
    } catch (e) {
      toast.error('تعذر فتح الموعد');
    }
  };

  const respond = async (status) => {
    try {
      await api.post(`/calendar/events/${detailEvent.id}/respond`, { status });
      toast.success('تم تسجيل ردك');
      const res = await api.get(`/calendar/events/${detailEvent.id}`);
      setDetailEvent({ ...res.data, occurrence_date: detailEvent.occurrence_date });
      fetchEvents(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const cancelEvent = async (scope) => {
    const params = new URLSearchParams({ scope });
    if (scope !== 'entire_series') params.set('occurrence_date', detailEvent.occurrence_date);
    try {
      await api.post(`/calendar/events/${detailEvent.id}/cancel?${params.toString()}`);
      toast.success('تم إلغاء الموعد');
      setDetailOpen(false);
      fetchEvents(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const convertToTask = async () => {
    if (!isOwner) return;
    const assignee = detailEvent.participants?.find((p) => p.participant_id !== detailEvent.created_by);
    if (!assignee) { toast.error('لا يوجد مشارك لتعيين المهمة له'); return; }
    try {
      await api.post('/owner/tasks', {
        title: detailEvent.title, description: detailEvent.description, priority: 'medium',
        assigned_to: assignee.participant_id, due_date: detailEvent.end_date, requires_proof: false,
      });
      toast.success('تم إنشاء مهمة من الموعد');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  // ---- Refinement 1: Open Conversation (reuses the existing Work Messages
  // thread via a persistent link on the event, never creates a duplicate). ----
  const openConversation = async () => {
    try {
      const res = await api.post(`/calendar/events/${detailEvent.id}/open-conversation`);
      const basePath = isOwner ? '/company-owner/messages' : '/employee/messages';
      window.location.href = `${basePath}?thread=${res.data.thread_id}`;
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  // ---- Refinement 2: final Attended/Absent, only after the meeting ends,
  // separate from the pre-meeting Accept/Decline/Tentative response. ----
  const meetingHasEnded = (ev) => ev && new Date(ev.occurrence_end || `${ev.end_date}T${ev.end_time || '23:59:59'}`) <= new Date();

  const markFinalAttendance = async (participantId, status) => {
    try {
      await api.post(`/calendar/events/${detailEvent.id}/participants/${participantId}/attendance`, { status });
      toast.success('تم تسجيل الحضور');
      const res = await api.get(`/calendar/events/${detailEvent.id}`);
      setDetailEvent({ ...res.data, occurrence_date: detailEvent.occurrence_date, occurrence_end: detailEvent.occurrence_end });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  // ---- Refinement 3: Meeting Notes - stored on the event only, no
  // notification is ever triggered by saving these. ----
  const [notesForm, setNotesForm] = useState({ summary: '', decisions: '', action_items: '' });
  const [savingNotes, setSavingNotes] = useState(false);
  const saveMeetingNotes = async () => {
    setSavingNotes(true);
    try {
      await api.put(`/calendar/events/${detailEvent.id}/notes`, notesForm);
      toast.success('تم حفظ محضر الاجتماع');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
    setSavingNotes(false);
  };

  // ---- Rendering helpers ----
  const eventsOnDay = (day) => events.filter((ev) => isSameDay(new Date(ev.occurrence_start), day));

  const EventChip = ({ ev }) => (
    <button
      onClick={() => openDetail(ev)}
      data-testid={`event-chip-${ev.id}-${ev.occurrence_date}`}
      className={`w-full text-start text-xs px-1.5 py-0.5 rounded-sm mb-0.5 truncate ${PRIORITY_ACCENT[ev.priority]} ${isOutsideWorkingHours(ev) ? 'ring-1 ring-amber-400' : ''}`}
      style={{ backgroundColor: `${ev.color}22`, color: ev.color }}
    >
      {!ev.all_day && ev.start_time && <span className="font-medium">{ev.start_time} </span>}
      {ev.title}
      {ev.display_status === 'cancelled' && <span className="line-through opacity-60"> (ملغى)</span>}
    </button>
  );

  const renderMonth = () => {
    const { start, end } = viewRange();
    const days = eachDayOfInterval({ start, end });
    const weekDayLabels = ['أحد', 'إثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت'];
    return (
      <div className="grid grid-cols-7 gap-px bg-gray-200 border border-gray-200 rounded-md overflow-hidden">
        {weekDayLabels.map((d) => (
          <div key={d} className="bg-gray-50 text-center text-xs font-medium text-gray-500 py-2">{d}</div>
        ))}
        {days.map((day) => (
          <div key={day.toISOString()} onClick={() => openCompose(day)}
            data-testid={`month-cell-${format(day, 'yyyy-MM-dd')}`}
            className={`bg-white min-h-[100px] p-1 cursor-pointer hover:bg-gray-50 ${!isSameMonth(day, currentDate) ? 'opacity-40' : ''}`}>
            <p className={`text-xs mb-1 ${isToday(day) ? 'w-5 h-5 flex items-center justify-center rounded-full bg-[#0033A0] text-white' : 'text-gray-500'}`}>
              {format(day, 'd')}
            </p>
            {eventsOnDay(day).slice(0, 3).map((ev) => <EventChip key={ev.id + ev.occurrence_date} ev={ev} />)}
            {eventsOnDay(day).length > 3 && <p className="text-[10px] text-gray-400">+{eventsOnDay(day).length - 3} أخرى</p>}
          </div>
        ))}
      </div>
    );
  };

  const renderWeekOrDay = () => {
    const { start, end } = viewRange();
    const days = view === 'day' ? [currentDate] : eachDayOfInterval({ start, end });
    return (
      <div className={`grid gap-px bg-gray-200 border border-gray-200 rounded-md overflow-hidden`} style={{ gridTemplateColumns: `repeat(${days.length}, 1fr)` }}>
        {days.map((day) => (
          <div key={day.toISOString()} className="bg-white min-h-[400px] p-2">
            <p className={`text-sm font-medium mb-2 text-center ${isToday(day) ? 'text-[#0033A0]' : 'text-gray-700'}`}>
              {format(day, 'EEE d')}
            </p>
            {eventsOnDay(day).map((ev) => <EventChip key={ev.id + ev.occurrence_date} ev={ev} />)}
            <button onClick={() => openCompose(day)} className="text-xs text-gray-400 hover:text-[#0033A0] mt-1">+ إضافة</button>
          </div>
        ))}
      </div>
    );
  };

  const renderAgenda = () => {
    const byDay = {};
    events.forEach((ev) => { (byDay[ev.occurrence_date] = byDay[ev.occurrence_date] || []).push(ev); });
    const sortedDates = Object.keys(byDay).sort();
    if (sortedDates.length === 0) return <Card className="p-12 text-center text-gray-500">لا توجد أحداث في هذه الفترة</Card>;
    return (
      <div className="space-y-4">
        {sortedDates.map((d) => (
          <Card key={d} className="p-4 bg-white border border-gray-200 rounded-md">
            <p className="text-sm font-bold text-[#0A0A0A] mb-2">{format(parseISO(d), 'EEEE, d MMMM yyyy')}</p>
            <div className="space-y-1">{byDay[d].map((ev) => <EventChip key={ev.id + ev.occurrence_date} ev={ev} />)}</div>
          </Card>
        ))}
      </div>
    );
  };

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-start flex-wrap gap-4">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="calendar-title">التقويم</h1>
            <p className="text-gray-600 mt-2">تقويم الشركة الذكي</p>
          </div>
          <Button onClick={() => openCompose()} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="new-event-btn">
            <Plus className="w-4 h-4 me-2" /> موعد جديد
          </Button>
        </div>

        <Card className="p-4 bg-white border border-gray-200 rounded-md flex flex-wrap items-center gap-3">
          <Button variant="outline" size="sm" onClick={goToday} data-testid="today-btn">اليوم</Button>
          <Button variant="outline" size="icon" onClick={goPrev} data-testid="prev-btn"><ChevronRight className="w-4 h-4" /></Button>
          <Button variant="outline" size="icon" onClick={goNext} data-testid="next-btn"><ChevronLeft className="w-4 h-4" /></Button>
          <p className="font-medium text-[#0A0A0A] min-w-[140px]">{format(currentDate, 'MMMM yyyy')}</p>

          <div className="flex items-center gap-1 border-s border-gray-200 ps-3">
            {['month', 'week', 'day', 'agenda'].map((v) => (
              <Button key={v} size="sm" variant={view === v ? 'default' : 'outline'} onClick={() => setView(v)}
                className={view === v ? 'bg-[#0033A0]' : ''} data-testid={`view-${v}-btn`}>
                {{ month: 'شهر', week: 'أسبوع', day: 'يوم', agenda: 'أجندة' }[v]}
              </Button>
            ))}
          </div>

          <div className="flex items-center gap-1 border-s border-gray-200 ps-3">
            <Input type="date" value={jumpDate} onChange={(e) => setJumpDate(e.target.value)} className="w-40" data-testid="jump-date-input" />
            <Button variant="outline" size="sm" onClick={jumpToDate}><CalendarDays className="w-4 h-4" /></Button>
          </div>

          <div className="flex items-center gap-1 border-s border-gray-200 ps-3 flex-1 min-w-[200px]">
            <Input placeholder="بحث..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()} data-testid="calendar-search-input" />
            <Button variant="outline" size="icon" onClick={runSearch}><Search className="w-4 h-4" /></Button>
          </div>
        </Card>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : (
          <>
            {view === 'month' && renderMonth()}
            {(view === 'week' || view === 'day') && renderWeekOrDay()}
            {view === 'agenda' && renderAgenda()}
          </>
        )}

        {/* ============ Search Results ============ */}
        <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
          <DialogContent className="max-w-lg max-h-[70vh] overflow-y-auto" data-testid="search-results-dialog">
            <DialogHeader><DialogTitle>نتائج البحث</DialogTitle></DialogHeader>
            {searchResults?.length === 0 ? (
              <p className="text-center text-gray-500 py-6">لا توجد نتائج</p>
            ) : (
              <div className="space-y-2">
                {searchResults?.map((ev) => (
                  <div key={ev.id} className="p-3 border border-gray-100 rounded-sm flex justify-between items-center" data-testid={`search-result-${ev.id}`}>
                    <div>
                      <p className="font-medium text-sm">{ev.title}</p>
                      <p className="text-xs text-gray-500">{ev.reference_number} · {CATEGORY_LABELS[ev.category]} · {ev.start_date}</p>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => { setSearchOpen(false); openDetail({ id: ev.id, occurrence_date: ev.start_date }); }}>عرض</Button>
                  </div>
                ))}
              </div>
            )}
          </DialogContent>
        </Dialog>

        {/* ============ Compose Dialog ============ */}
        <Dialog open={composeOpen} onOpenChange={setComposeOpen}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="compose-event-dialog">
            <DialogHeader><DialogTitle>{editingEvent ? 'تعديل الموعد' : 'موعد جديد'}</DialogTitle></DialogHeader>
            <div className="space-y-4">
              {conflictWarning && (
                <Card className="p-3 bg-amber-50 border border-amber-200 rounded-md" data-testid="conflict-warning">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                    <div className="text-xs text-amber-800">
                      <p className="font-medium mb-1">تم اكتشاف تعارض في الموعد</p>
                      {conflictWarning.conflicts?.map((c, i) => <p key={i}>- {c.title} ({c.occurrence_date}) مع {c.participants?.join('، ')}</p>)}
                      {conflictWarning.holiday_conflict && <p>- يتعارض مع عطلة رسمية: {conflictWarning.holiday_conflict.title}</p>}
                      <Button size="sm" className="mt-2 bg-amber-600 hover:bg-amber-700 text-white" onClick={() => submitCompose(true)} data-testid="override-conflict-btn">
                        تجاوز والحفظ رغم التعارض
                      </Button>
                    </div>
                  </div>
                </Card>
              )}

              {editingEvent && editingEvent.recurrence_type !== 'none' && (
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">نطاق التعديل</label>
                  <select className={selectClass} value={editScope} onChange={(e) => setEditScope(e.target.value)} data-testid="edit-scope-select">
                    <option value="this_event_only">هذا الموعد فقط</option>
                    <option value="this_and_future">هذا والمواعيد القادمة</option>
                    <option value="entire_series">السلسلة بأكملها</option>
                  </select>
                </div>
              )}

              <Input placeholder="العنوان" value={compose.title} onChange={(e) => setCompose({ ...compose, title: e.target.value })} data-testid="event-title-input" />
              <Textarea placeholder="الوصف" rows={3} value={compose.description} onChange={(e) => setCompose({ ...compose, description: e.target.value })} data-testid="event-description-input" />

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">التصنيف</label>
                  <select className={selectClass} value={compose.category} onChange={(e) => setCompose({ ...compose, category: e.target.value })} data-testid="event-category-select">
                    {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الأولوية</label>
                  <select className={selectClass} value={compose.priority} onChange={(e) => setCompose({ ...compose, priority: e.target.value })} data-testid="event-priority-select">
                    {Object.entries(PRIORITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
              </div>

              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={compose.all_day} onCheckedChange={(v) => setCompose({ ...compose, all_day: !!v })} data-testid="event-all-day-checkbox" /> طوال اليوم
              </label>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ البدء</label>
                  <Input type="date" value={compose.start_date} onChange={(e) => setCompose({ ...compose, start_date: e.target.value })} data-testid="event-start-date" />
                </div>
                {!compose.all_day && (
                  <div>
                    <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">وقت البدء</label>
                    <Input type="time" value={compose.start_time} onChange={(e) => setCompose({ ...compose, start_time: e.target.value })} data-testid="event-start-time" />
                  </div>
                )}
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">تاريخ الانتهاء</label>
                  <Input type="date" value={compose.end_date} onChange={(e) => setCompose({ ...compose, end_date: e.target.value })} data-testid="event-end-date" />
                </div>
                {!compose.all_day && (
                  <div>
                    <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">وقت الانتهاء</label>
                    <Input type="time" value={compose.end_time} onChange={(e) => setCompose({ ...compose, end_time: e.target.value })} data-testid="event-end-time" />
                  </div>
                )}
              </div>

              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">نوع الموقع</label>
                <select className={selectClass} value={compose.location_type} onChange={(e) => setCompose({ ...compose, location_type: e.target.value })} data-testid="event-location-type-select">
                  <option value="">بدون تحديد</option>
                  {Object.entries(LOCATION_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              {compose.location_type === 'online' && (
                <Input placeholder="رابط الاجتماع" value={compose.online_link} onChange={(e) => setCompose({ ...compose, online_link: e.target.value })}
                  className="flex items-center" data-testid="event-online-link-input" />
              )}
              {(compose.location_type === 'client_site' || compose.location_type === 'other') && (
                <Input placeholder="الموقع" value={compose.location} onChange={(e) => setCompose({ ...compose, location: e.target.value })} data-testid="event-location-input" />
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الظهور</label>
                  <select className={selectClass} value={compose.visibility} onChange={(e) => setCompose({ ...compose, visibility: e.target.value })} data-testid="event-visibility-select">
                    {Object.entries(VISIBILITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">المدعوون</label>
                  <select className={selectClass} value={compose.recipient_type} onChange={(e) => setCompose({ ...compose, recipient_type: e.target.value, recipient_ids: [] })} data-testid="event-recipient-type-select">
                    <option value="owner">المالك</option>
                    <option value="employee">موظف / موظفين</option>
                    <option value="department">قسم واحد أو أكثر</option>
                    <option value="owner_plus_employees">المالك + موظفين</option>
                    <option value="company">الشركة بأكملها</option>
                  </select>
                </div>
              </div>

              {(compose.recipient_type === 'employee' || compose.recipient_type === 'owner_plus_employees') && (
                <div className="border border-gray-200 rounded-md max-h-32 overflow-y-auto p-2 space-y-1" data-testid="event-employee-list">
                  {employees.map((emp) => (
                    <label key={emp.id} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={compose.recipient_ids.includes(emp.id)} onCheckedChange={() => toggleRecipient(emp.id)} /> {emp.name}
                    </label>
                  ))}
                </div>
              )}
              {compose.recipient_type === 'department' && (
                <div className="border border-gray-200 rounded-md max-h-32 overflow-y-auto p-2 space-y-1" data-testid="event-department-list">
                  {departments.map((d) => (
                    <label key={d} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={compose.recipient_ids.includes(d)} onCheckedChange={() => toggleRecipient(d)} /> {d}
                    </label>
                  ))}
                </div>
              )}

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">التكرار</label>
                  <select className={selectClass} value={compose.recurrence_type} onChange={(e) => setCompose({ ...compose, recurrence_type: e.target.value })} data-testid="event-recurrence-select">
                    {Object.entries(RECURRENCE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                {compose.recurrence_type !== 'none' && (
                  <>
                    <div>
                      <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">كل</label>
                      <Input type="number" min="1" value={compose.recurrence_interval} onChange={(e) => setCompose({ ...compose, recurrence_interval: e.target.value })} />
                    </div>
                    <div>
                      <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">ينتهي</label>
                      <select className={selectClass} value={compose.recurrence_end_type} onChange={(e) => setCompose({ ...compose, recurrence_end_type: e.target.value })}>
                        <option value="never">أبداً</option>
                        <option value="after_count">بعد عدد مرات</option>
                        <option value="on_date">في تاريخ</option>
                      </select>
                    </div>
                  </>
                )}
              </div>
              {compose.recurrence_type !== 'none' && compose.recurrence_end_type === 'after_count' && (
                <Input type="number" min="1" placeholder="عدد المرات" value={compose.recurrence_end_value} onChange={(e) => setCompose({ ...compose, recurrence_end_value: e.target.value })} />
              )}
              {compose.recurrence_type !== 'none' && compose.recurrence_end_type === 'on_date' && (
                <Input type="date" value={compose.recurrence_end_value} onChange={(e) => setCompose({ ...compose, recurrence_end_value: e.target.value })} />
              )}

              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">التذكيرات</label>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(REMINDER_PRESET_LABELS).map(([k, v]) => (
                    <button key={k} type="button" onClick={() => addPendingReminder(k)}
                      className={`px-2.5 py-1 rounded-full text-xs border ${pendingReminders.some((r) => r.preset === k) ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-600 border-gray-200'}`}>
                      {v}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-3 pt-2 border-t border-gray-100">
                <Button onClick={() => submitCompose(false)} disabled={saving} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="save-event-btn">
                  {saving ? 'جارٍ الحفظ...' : editingEvent ? 'حفظ التعديلات' : 'إنشاء الموعد'}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* ============ Event Detail Dialog ============ */}
        <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
          <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" data-testid="event-detail-dialog">
            {detailEvent && (
              <>
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-gray-400 font-mono">{detailEvent.reference_number}</span>
                    {detailEvent.title}
                  </DialogTitle>
                </DialogHeader>
                <div className="space-y-3 text-sm">
                  <p className="text-gray-600">{detailEvent.description}</p>
                  <div className="flex flex-wrap gap-3 text-xs text-gray-500">
                    <span>{CATEGORY_LABELS[detailEvent.category]}</span>
                    <span>· {PRIORITY_LABELS[detailEvent.priority]}</span>
                    <span>· {detailEvent.all_day ? 'طوال اليوم' : `${detailEvent.start_time || ''} - ${detailEvent.end_time || ''}`}</span>
                    <span>· {detailEvent.start_date}</span>
                    {detailEvent.recurrence_type !== 'none' && <span className="flex items-center gap-1"><Repeat className="w-3 h-3" /> {RECURRENCE_LABELS[detailEvent.recurrence_type]}</span>}
                  </div>
                  {detailEvent.location_type === 'online' && detailEvent.online_link && (
                    <a href={detailEvent.online_link} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[#0033A0] text-xs">
                      <Video className="w-3.5 h-3.5" /> {detailEvent.online_link}
                    </a>
                  )}
                  {detailEvent.location_type === 'office' && (
                    <p className="flex items-center gap-1 text-xs text-gray-600"><Building2 className="w-3.5 h-3.5" /> موقع الشركة</p>
                  )}
                  {detailEvent.location && detailEvent.location_type !== 'online' && (
                    <p className="flex items-center gap-1 text-xs text-gray-600"><MapPin className="w-3.5 h-3.5" /> {detailEvent.location}</p>
                  )}

                  {isOwner && detailEvent.participants?.length > 0 && (
                    <Card className="p-3 bg-gray-50 border border-gray-100" data-testid="participant-responses">
                      <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><Users className="w-3.5 h-3.5" /> ردود المشاركين</p>
                      {detailEvent.participants.map((p) => (
                        <div key={p.id} className="flex justify-between items-center text-xs py-1">
                          <span>{p.participant_name}</span>
                          <div className="flex items-center gap-2">
                            <span className={p.attendance_status === 'accepted' ? 'text-green-600' : p.attendance_status === 'declined' ? 'text-red-600' : 'text-gray-500'}>
                              {RESPONSE_LABELS[p.attendance_status]}
                            </span>
                            {/* Refinement 2: final Attended/Absent - only offered once the
                                meeting has actually finished, distinct from the RSVP above. */}
                            {meetingHasEnded(detailEvent) && (
                              p.final_attendance ? (
                                <span className={`px-1.5 py-0.5 rounded text-[10px] border ${p.final_attendance === 'attended' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200'}`} data-testid={`final-attendance-${p.id}`}>
                                  {p.final_attendance === 'attended' ? 'حضر' : 'غائب'}
                                </span>
                              ) : (
                                <div className="flex gap-1">
                                  <button onClick={() => markFinalAttendance(p.participant_id, 'attended')} className="text-[10px] px-1.5 py-0.5 rounded border border-green-200 text-green-700 hover:bg-green-50" data-testid={`mark-attended-${p.id}`}>حضر</button>
                                  <button onClick={() => markFinalAttendance(p.participant_id, 'absent')} className="text-[10px] px-1.5 py-0.5 rounded border border-red-200 text-red-700 hover:bg-red-50" data-testid={`mark-absent-${p.id}`}>غائب</button>
                                </div>
                              )
                            )}
                          </div>
                        </div>
                      ))}
                    </Card>
                  )}

                  {/* Refinement 3: Meeting Notes - stored only on the event, saving never notifies anyone. */}
                  <Card className="p-3 bg-white border border-gray-100" data-testid="meeting-notes-card">
                    <p className="text-xs font-medium text-gray-500 mb-2">محضر الاجتماع</p>
                    <div className="space-y-2">
                      <div>
                        <label className="text-[11px] text-gray-400">الملخص</label>
                        <Textarea rows={2} value={notesForm.summary} onChange={(e) => setNotesForm({ ...notesForm, summary: e.target.value })} data-testid="notes-summary-input" />
                      </div>
                      <div>
                        <label className="text-[11px] text-gray-400">القرارات</label>
                        <Textarea rows={2} value={notesForm.decisions} onChange={(e) => setNotesForm({ ...notesForm, decisions: e.target.value })} data-testid="notes-decisions-input" />
                      </div>
                      <div>
                        <label className="text-[11px] text-gray-400">بنود العمل</label>
                        <Textarea rows={2} value={notesForm.action_items} onChange={(e) => setNotesForm({ ...notesForm, action_items: e.target.value })} data-testid="notes-action-items-input" />
                      </div>
                      <Button size="sm" onClick={saveMeetingNotes} disabled={savingNotes} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="save-notes-btn">
                        {savingNotes ? 'جارٍ الحفظ...' : 'حفظ المحضر'}
                      </Button>
                    </div>
                  </Card>

                  {detailEvent.is_participant && (
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => respond('accepted')} className="bg-green-600 hover:bg-green-700 text-white rounded-sm" data-testid="respond-accept-btn">
                        <CheckCircle2 className="w-3.5 h-3.5 me-1" /> قبول
                      </Button>
                      <Button size="sm" onClick={() => respond('declined')} variant="outline" className="rounded-sm text-red-600" data-testid="respond-decline-btn">
                        <XCircle className="w-3.5 h-3.5 me-1" /> رفض
                      </Button>
                      <Button size="sm" onClick={() => respond('tentative')} variant="outline" className="rounded-sm" data-testid="respond-tentative-btn">
                        <HelpCircle className="w-3.5 h-3.5 me-1" /> مبدئي
                      </Button>
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2 border-t border-gray-100 pt-3">
                    <Button size="sm" variant="outline" className="rounded-sm" onClick={() => openEditCompose(detailEvent)} data-testid="edit-event-btn">تعديل</Button>
                    {detailEvent.recurrence_type !== 'none' ? (
                      <>
                        <Button size="sm" variant="outline" className="rounded-sm text-red-600" onClick={() => cancelEvent('this_event_only')} data-testid="cancel-occurrence-btn">
                          إلغاء هذا الموعد فقط
                        </Button>
                        <Button size="sm" variant="outline" className="rounded-sm text-red-600" onClick={() => cancelEvent('entire_series')} data-testid="cancel-series-btn">
                          إلغاء السلسلة بأكملها
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="outline" className="rounded-sm text-red-600" onClick={() => cancelEvent('entire_series')} data-testid="cancel-event-btn">
                        إلغاء الموعد
                      </Button>
                    )}
                    {isOwner && (
                      <Button size="sm" variant="outline" className="rounded-sm" onClick={convertToTask} data-testid="convert-event-to-task-btn">تحويل إلى مهمة</Button>
                    )}
                    <Button size="sm" variant="outline" className="rounded-sm" onClick={openConversation} data-testid="open-conversation-btn">
                      فتح المحادثة
                    </Button>
                  </div>
                  <p className="text-[11px] text-gray-400">
                    الإلغاء إجراء ناعم دائماً - لا يُحذف أي موعد نهائياً، ويبقى قابلاً للبحث والمراجعة في سجل النشاط.
                  </p>
                </div>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default CalendarPage;
