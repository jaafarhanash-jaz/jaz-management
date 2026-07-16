import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatDistanceToNow } from 'date-fns';
import { ar } from 'date-fns/locale';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { useNotifications } from '@/hooks/useNotifications';
import { categoryMeta, fallbackActionUrl } from '@/constants/notifications';
import {
  CheckCircle2, Clock, Bell, CalendarDays, Timer, Gift, MailQuestion, PartyPopper,
  Zap, Siren, ArrowUpCircle, ListChecks, Sparkles, LogIn, LogOut, MessageSquare,
  FileText, Sunrise, Sun, Moon, ArrowLeft, Inbox, PartyPopper as ConfettiIcon,
} from 'lucide-react';

// ---- Static lookup tables (shared shape/labels with the Owner side where
// the same concepts exist, kept local since this page's visual language is
// intentionally different - a workspace, not a control center). ----
const PRIORITY_META = {
  critical: { label: 'عاجلة', color: 'text-red-700', bg: 'bg-red-50', border: 'border-red-200' },
  high: { label: 'عالية', color: 'text-orange-700', bg: 'bg-orange-50', border: 'border-orange-200' },
  medium: { label: 'متوسطة', color: 'text-yellow-700', bg: 'bg-yellow-50', border: 'border-yellow-200' },
  low: { label: 'منخفضة', color: 'text-blue-700', bg: 'bg-blue-50', border: 'border-blue-200' },
};
const STATUS_LABELS = {
  new: 'جديدة', received: 'تم الاستلام', seen: 'تمت المشاهدة', in_progress: 'قيد التنفيذ',
  pending_review: 'بانتظار المراجعة', completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة',
};
// Progress is derived purely from the task's real status - not a fabricated
// number - just a visual translation of the same state the badge shows.
const STATUS_PROGRESS = { new: 5, received: 15, seen: 20, in_progress: 55, pending_review: 85, rejected: 35, overdue: 25, completed: 100 };

const FOCUS_META = {
  critical: { label: 'مهمة عاجلة تحتاج انتباهك', icon: Siren, accent: 'border-s-red-500', chipBg: 'bg-red-50', chipColor: 'text-red-700', wash: 'from-red-50 via-red-50/20 to-white' },
  urgent: { label: 'مهمة فورية بانتظارك', icon: Zap, accent: 'border-s-orange-500', chipBg: 'bg-orange-50', chipColor: 'text-orange-700', wash: 'from-orange-50 via-orange-50/20 to-white' },
  high: { label: 'أولوية عالية', icon: ArrowUpCircle, accent: 'border-s-amber-500', chipBg: 'bg-amber-50', chipColor: 'text-amber-700', wash: 'from-amber-50 via-amber-50/20 to-white' },
  nearest_due: { label: 'الأقرب موعد تسليم', icon: Clock, accent: 'border-s-[#0033A0]', chipBg: 'bg-blue-50', chipColor: 'text-[#0033A0]', wash: 'from-blue-50 via-blue-50/20 to-white' },
};

const TASK_FILTERS = [
  { key: 'all', label: 'الكل' },
  { key: 'critical', label: 'عاجلة' },
  { key: 'urgent', label: 'فورية' },
  { key: 'high', label: 'عالية الأولوية' },
  { key: 'normal', label: 'عادية' },
  { key: 'today', label: 'اليوم' },
  { key: 'upcoming', label: 'قادمة' },
];

const todayStr = () => new Date().toISOString().slice(0, 10);

const getGreeting = () => {
  const h = new Date().getHours();
  if (h >= 5 && h < 12) return { text: 'صباح الخير', icon: Sunrise };
  if (h >= 12 && h < 17) return { text: 'مساء الخير', icon: Sun };
  return { text: 'طاب مساؤك', icon: Moon };
};

const formatMinutes = (minutes) => {
  if (minutes === null || minutes === undefined) return '-';
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h === 0) return `${m} دقيقة`;
  return `${h} ساعة${m > 0 ? ` و${m} دقيقة` : ''}`;
};

const formatClock = (iso) => iso ? new Date(iso).toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' }) : '-';

// ---- Shared presentational pieces (pure UI, no state/logic of their own) -
// used across every side widget so padding/radius/shadow/hover/header style
// stay identical everywhere instead of being repeated four times. ----
const CARD_SURFACE = 'bg-white border border-gray-100 rounded-xl shadow-sm';

const Widget = ({ icon: Icon, title, action, children, testId }) => (
  <Card className={`${CARD_SURFACE} p-5 hover:shadow-md transition-shadow duration-200`} data-testid={testId}>
    <div className="flex items-center justify-between mb-4">
      <h3 className="text-[15px] font-bold text-[#0A0A0A] flex items-center gap-2">
        <Icon className="w-4 h-4 text-[#0033A0]" /> {title}
      </h3>
      {action}
    </div>
    {children}
  </Card>
);

const EMPTY_TONE = {
  gray: 'bg-gray-50 text-gray-300',
  blue: 'bg-blue-50 text-blue-300',
  emerald: 'bg-emerald-50 text-emerald-400',
};

const EmptyState = ({ icon: Icon, title, subtitle, tone = 'gray', size = 'md' }) => (
  <div className={`text-center ${size === 'lg' ? 'py-6' : 'py-4'}`}>
    <div className={`mx-auto rounded-full flex items-center justify-center mb-3 ${EMPTY_TONE[tone]} ${size === 'lg' ? 'w-16 h-16' : 'w-12 h-12'}`}>
      <Icon className={size === 'lg' ? 'w-7 h-7' : 'w-5 h-5'} />
    </div>
    {title && <p className={`font-bold text-[#0A0A0A] ${size === 'lg' ? 'text-base' : 'text-sm'}`}>{title}</p>}
    <p className={`text-gray-400 ${size === 'lg' ? 'text-sm mt-1' : 'text-xs'}`}>{subtitle}</p>
  </div>
);

const FOCUS_RING = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0033A0]/50 focus-visible:ring-offset-1';

const EmployeeDashboard = ({ onLogout, language, setLanguage }) => {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);
  const [employeeName, setEmployeeName] = useState('');
  const [taskFilter, setTaskFilter] = useState('all');
  const [tick, setTick] = useState(0); // forces a re-render every minute so live-elapsed working hours stay current

  const { notifications, unreadCount, loading: notificationsLoading, markAsRead } = useNotifications('employee');

  useEffect(() => {
    fetchDashboard();
    api.get('/auth/me').then((res) => setEmployeeName(res.data.name)).catch(() => {});
    const interval = setInterval(() => fetchDashboard(true), 30000);
    const clock = setInterval(() => setTick((n) => n + 1), 60000);
    return () => { clearInterval(interval); clearInterval(clock); };
  }, []);

  const fetchDashboard = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const response = await api.get('/employee/dashboard');
      setDashboard(response.data);
    } catch (error) {
      console.error('Error fetching dashboard:', error);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const greeting = getGreeting();
  const todayFormatted = new Date().toLocaleDateString('ar-EG', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

  const tasks = useMemo(() => dashboard?.tasks || [], [dashboard]);
  const dueTodayCount = tasks.filter((tk) => tk.due_date === todayStr()).length;
  const criticalCount = tasks.filter((tk) => tk.priority === 'critical').length;
  const urgentCount = tasks.filter((tk) => tk.task_category === 'urgent').length;

  const summaryText = tasks.length === 0
    ? 'لا توجد مهام نشطة حالياً - استمتع بيومك.'
    : `لديك ${dueTodayCount} ${dueTodayCount === 1 ? 'مهمة' : 'مهام'} اليوم` +
      (criticalCount > 0 ? `، منها ${criticalCount} عاجلة` : '') +
      (urgentCount > 0 ? `${criticalCount > 0 ? ' و' : '، منها '}${urgentCount} فورية` : '') + '.';

  const taskGroups = useMemo(() => {
    const t_ = todayStr();
    return {
      all: tasks,
      critical: tasks.filter((tk) => tk.priority === 'critical'),
      urgent: tasks.filter((tk) => tk.task_category === 'urgent'),
      high: tasks.filter((tk) => tk.priority === 'high'),
      normal: tasks.filter((tk) => tk.priority !== 'critical' && tk.priority !== 'high' && tk.task_category !== 'urgent'),
      today: tasks.filter((tk) => tk.due_date === t_),
      upcoming: tasks.filter((tk) => tk.due_date && tk.due_date > t_),
    };
  }, [tasks]);
  const visibleTasks = taskGroups[taskFilter] || [];

  const attendance = dashboard?.attendance;
  const workingMinutes = useMemo(() => {
    if (!attendance?.check_in_time) return null;
    const start = new Date(attendance.check_in_time);
    const end = attendance.check_out_time ? new Date(attendance.check_out_time) : new Date();
    return Math.max(0, (end - start) / 60000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attendance, tick]);
  const nextAttendanceAction = !attendance?.check_in_time ? 'check_in' : !attendance?.check_out_time ? 'check_out' : 'done';
  const lastActivityIso = attendance?.check_out_time || attendance?.check_in_time || null;

  const messagesWidget = dashboard?.messages_widget;

  const openTask = (taskId) => navigate(`/employee/tasks?highlight=${taskId}`);

  const handleNotificationClick = (n) => {
    if (!n.read_status) markAsRead(n.id);
    navigate(n.action_url || fallbackActionUrl(n.category, 'employee'));
  };

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-8">
        {loading ? (
          <div className="space-y-8" data-testid="employee-dashboard-loading">
            <div className="h-40 rounded-2xl bg-gray-100 animate-pulse" />
            <div>
              <div className="h-5 w-28 rounded bg-gray-100 animate-pulse mb-3" />
              <div className="h-32 rounded-xl bg-gray-100 animate-pulse" />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="lg:col-span-2 space-y-3">
                <div className="flex gap-2">
                  {[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-7 w-16 rounded-full bg-gray-100 animate-pulse" />)}
                </div>
                {[0, 1, 2].map((i) => <div key={i} className="h-20 rounded-xl bg-gray-100 animate-pulse" />)}
              </div>
              <div className="space-y-6">
                <div className="h-48 rounded-xl bg-gray-100 animate-pulse" />
                <div className="h-40 rounded-xl bg-gray-100 animate-pulse" />
                <div className="h-40 rounded-xl bg-gray-100 animate-pulse" />
              </div>
            </div>
          </div>
        ) : !dashboard ? (
          <div className="text-center py-16 text-gray-500" data-testid="employee-dashboard-error">
            <p className="mb-4">تعذر تحميل بيانات لوحة التحكم. تحقق من الاتصال وحاول مرة أخرى.</p>
            <Button onClick={() => fetchDashboard()} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-md" data-testid="employee-dashboard-retry-btn">
              إعادة المحاولة
            </Button>
          </div>
        ) : (
          <>
            {/* ---- Hero ---- */}
            <div
              className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#0033A0] via-[#00287d] to-[#001c4d] text-white p-6 md:p-9 shadow-md animate-in fade-in slide-in-from-top-2 duration-500"
              data-testid="employee-hero"
            >
              <div className="relative z-10">
                <div className="flex items-center gap-2 text-blue-100 text-sm mb-2">
                  <greeting.icon className="w-4 h-4" />
                  <span>{greeting.text}</span>
                </div>
                <h1 className="text-2xl md:text-3xl font-bold mb-1.5 tracking-tight" data-testid="employee-dashboard-title">
                  {employeeName || t('dashboard', language)} 👋
                </h1>
                <p className="text-blue-100/90 text-sm">{todayFormatted}</p>
                <p className="text-blue-50/90 mt-3 text-sm max-w-xl leading-relaxed">{summaryText}</p>
              </div>
              <Sparkles className="absolute -left-6 -bottom-6 w-36 h-36 text-white/[0.06] rotate-12" />
              <div className="absolute top-0 end-0 w-40 h-40 bg-white/[0.04] rounded-full -translate-y-1/2 translate-x-1/3" />
            </div>

            {/* Company Holiday banner */}
            {dashboard.today_is_holiday && (
              <Card className="p-4 bg-gradient-to-l from-emerald-50 to-white border border-emerald-200 rounded-xl shadow-sm flex items-center gap-3 animate-in fade-in duration-300" data-testid="today-holiday-banner">
                <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0">
                  <PartyPopper className="w-5 h-5 text-emerald-600" />
                </div>
                <p className="text-emerald-800 font-medium text-sm">
                  {dashboard.today_holiday_type === 'weekly_holiday'
                    ? 'اليوم عطلة أسبوعية'
                    : `اليوم عطلة رسمية للشركة${dashboard.today_holiday_title ? `: ${dashboard.today_holiday_title}` : ''}`}
                </p>
              </Card>
            )}

            {/* ---- Focus Now ---- */}
            <div data-testid="focus-now-section">
              <h2 className="text-xl font-bold text-[#0A0A0A] mb-3 flex items-center gap-2 tracking-tight">
                <Sparkles className="w-5 h-5 text-[#0033A0]" /> ركّز الآن
              </h2>
              {dashboard.focus_task ? (
                (() => {
                  const meta = FOCUS_META[dashboard.focus_reason] || FOCUS_META.nearest_due;
                  const Icon = meta.icon;
                  const task = dashboard.focus_task;
                  return (
                    <Card
                      className={`p-6 md:p-7 bg-gradient-to-br ${meta.wash} border border-gray-100 rounded-xl shadow-md border-s-4 ${meta.accent} animate-in fade-in slide-in-from-bottom-1 duration-300`}
                      data-testid="focus-task-card"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-4">
                        <div className="flex-1 min-w-[200px]">
                          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${meta.chipBg} ${meta.chipColor} mb-3`}>
                            <Icon className="w-3.5 h-3.5" /> {meta.label}
                          </span>
                          <h3 className="text-xl font-bold text-[#0A0A0A] tracking-tight">{task.title}</h3>
                          {task.description && <p className="text-sm text-gray-600 mt-1.5 line-clamp-2 leading-relaxed">{task.description}</p>}
                          <div className="flex flex-wrap gap-4 mt-4 text-xs text-gray-500">
                            {task.due_date && <span>موعد التسليم: <b className="text-gray-700 font-semibold">{task.due_date}{task.due_time ? ` ${task.due_time}` : ''}</b></span>}
                            {task.created_by_name && <span>بواسطة: <b className="text-gray-700 font-semibold">{task.created_by_name}</b></span>}
                          </div>
                        </div>
                        <Button
                          onClick={() => openTask(task.id)}
                          className="bg-[#0033A0] hover:bg-[#002277] rounded-md flex-shrink-0 shadow-sm hover:shadow transition-all"
                          data-testid="focus-task-open-btn"
                        >
                          فتح المهمة <ArrowLeft className="w-4 h-4 ms-2" />
                        </Button>
                      </div>
                    </Card>
                  );
                })()
              ) : (
                <Card className={`${CARD_SURFACE} p-10 text-center animate-in fade-in duration-300`} data-testid="focus-task-empty">
                  <EmptyState icon={ConfettiIcon} tone="emerald" size="lg" title="أنت على اطلاع بكل شيء" subtitle="لا توجد مهام تحتاج انتباهك الآن." />
                </Card>
              )}
            </div>

            {/* ---- Main grid: My Tasks (wide) + side widgets ---- */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* My Tasks */}
              <div className="lg:col-span-2 space-y-4" data-testid="my-tasks-section">
                <h2 className="text-xl font-bold text-[#0A0A0A] flex items-center gap-2 tracking-tight">
                  <ListChecks className="w-5 h-5 text-[#0033A0]" /> مهامي
                </h2>
                <div className="flex flex-wrap gap-2">
                  {TASK_FILTERS.map((f) => (
                    <button
                      key={f.key}
                      type="button"
                      onClick={() => setTaskFilter(f.key)}
                      data-testid={`task-filter-${f.key}`}
                      className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all duration-150 ${FOCUS_RING} ${
                        taskFilter === f.key
                          ? 'bg-[#0033A0] text-white border-[#0033A0] shadow-sm'
                          : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300'
                      }`}
                    >
                      {f.label} {taskGroups[f.key]?.length > 0 && <span className="opacity-70">({taskGroups[f.key].length})</span>}
                    </button>
                  ))}
                </div>

                {visibleTasks.length === 0 ? (
                  <Card className={`${CARD_SURFACE} p-8`}>
                    <EmptyState icon={ListChecks} tone="gray" subtitle="لا توجد مهام في هذا التصنيف" />
                  </Card>
                ) : (
                  <div className="space-y-3">
                    {visibleTasks.map((task) => {
                      const pMeta = PRIORITY_META[task.priority] || PRIORITY_META.medium;
                      const isCritical = task.priority === 'critical';
                      return (
                        <Card
                          key={task.id}
                          className={`p-4 bg-white border rounded-xl task-card shadow-sm hover:shadow-md transition-shadow duration-200 ${isCritical ? 'border-red-100 border-s-4 border-s-red-500' : 'border-gray-100'}`}
                          data-testid={`workspace-task-${task.id}`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center flex-wrap gap-2 mb-1.5">
                                <h4 className="font-semibold text-[#0A0A0A] truncate">{task.title}</h4>
                                <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium border ${pMeta.bg} ${pMeta.color} ${pMeta.border}`}>{pMeta.label}</span>
                                <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-gray-50 text-gray-600 border border-gray-200">
                                  {STATUS_LABELS[task.status] || task.status}
                                </span>
                              </div>
                              <div className="flex flex-wrap gap-3 text-xs text-gray-500 mb-2.5">
                                {task.due_date && <span>التسليم: <b className="text-gray-700 font-medium">{task.due_date}{task.due_time ? ` ${task.due_time}` : ''}</b></span>}
                                {task.created_by_name && <span>بواسطة: <b className="text-gray-700 font-medium">{task.created_by_name}</b></span>}
                              </div>
                              <div className="h-1.5 w-full max-w-[220px] bg-gray-100 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full transition-all duration-500 ${isCritical ? 'bg-red-500' : 'bg-[#0033A0]'}`}
                                  style={{ width: `${STATUS_PROGRESS[task.status] ?? 0}%` }}
                                />
                              </div>
                            </div>
                            <Button size="sm" variant="outline" onClick={() => openTask(task.id)} className="flex-shrink-0 rounded-md" data-testid={`open-task-${task.id}`}>
                              فتح
                            </Button>
                          </div>
                        </Card>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Side widgets */}
              <div className="space-y-6">
                {/* Attendance */}
                <Widget icon={Clock} title="الحضور اليوم" testId="attendance-widget">
                  <div className="flex items-center gap-2 mb-4">
                    <span className={`w-2 h-2 rounded-full ${nextAttendanceAction === 'check_out' ? 'bg-emerald-500' : nextAttendanceAction === 'done' ? 'bg-gray-300' : 'bg-amber-400 animate-pulse'}`} />
                    <span className="text-sm text-gray-700 font-medium">
                      {nextAttendanceAction === 'check_in' && 'لم تسجل حضورك بعد'}
                      {nextAttendanceAction === 'check_out' && 'أنت الآن في العمل'}
                      {nextAttendanceAction === 'done' && 'أنهيت يوم عملك'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5 mb-4">
                    {[
                      { label: 'دخول', value: formatClock(attendance?.check_in_time), icon: LogIn },
                      { label: 'خروج', value: formatClock(attendance?.check_out_time), icon: LogOut },
                      { label: 'ساعات العمل', value: formatMinutes(workingMinutes), icon: Timer },
                      { label: 'آخر نشاط', value: formatClock(lastActivityIso), icon: Clock },
                    ].map((stat) => (
                      <div key={stat.label} className="bg-gray-50 rounded-lg p-2.5">
                        <div className="flex items-center gap-1.5 text-gray-400 mb-1">
                          <stat.icon className="w-3 h-3" />
                          <span className="text-[11px]">{stat.label}</span>
                        </div>
                        <p className="text-sm font-bold text-[#0A0A0A]">{stat.value}</p>
                      </div>
                    ))}
                  </div>
                  {nextAttendanceAction === 'done' ? (
                    <div className="flex items-center gap-2 text-emerald-700 text-sm bg-emerald-50 rounded-lg px-3 py-2.5">
                      <CheckCircle2 className="w-4 h-4 flex-shrink-0" /> تم تسجيل الحضور والانصراف
                    </div>
                  ) : (
                    <Button
                      onClick={() => navigate('/employee/attendance')}
                      className={`w-full rounded-md text-white shadow-sm hover:shadow transition-all ${nextAttendanceAction === 'check_out' ? 'bg-orange-600 hover:bg-orange-700' : 'bg-[#0033A0] hover:bg-[#002277]'}`}
                      data-testid="attendance-cta-btn"
                    >
                      {nextAttendanceAction === 'check_in' ? <LogIn className="w-4 h-4 me-2" /> : <LogOut className="w-4 h-4 me-2" />}
                      {nextAttendanceAction === 'check_in' ? 'تسجيل الحضور' : 'تسجيل الانصراف'}
                    </Button>
                  )}
                </Widget>

                {/* Messages */}
                <Widget icon={MessageSquare} title="الرسائل" testId="messages-widget">
                  {messagesWidget?.unread_conversations > 0 ? (
                    <>
                      <div className="flex items-baseline gap-1.5 mb-3">
                        <p className="text-3xl font-bold text-[#0A0A0A] tracking-tight">{messagesWidget.unread_conversations}</p>
                        <p className="text-xs text-gray-500">محادثة غير مقروءة</p>
                      </div>
                      <div className="space-y-2.5 mb-4">
                        {messagesWidget.recent.map((m, i) => (
                          <div key={i} className="text-xs border-t border-gray-100 pt-2.5 first:border-0 first:pt-0">
                            <p className="font-semibold text-[#0A0A0A] truncate">{m.sender_name} · {m.subject}</p>
                            <p className="text-gray-500 truncate mt-0.5">{m.preview}</p>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <EmptyState icon={Inbox} tone="gray" subtitle="لا توجد رسائل جديدة" />
                  )}
                  <Button onClick={() => navigate('/employee/messages')} variant="outline" className="w-full rounded-md" data-testid="messages-cta-btn">
                    فتح الرسائل
                  </Button>
                </Widget>

                {/* Notifications */}
                <Widget
                  icon={Bell}
                  title="الإشعارات"
                  testId="notifications-widget"
                  action={unreadCount > 0 && (
                    <span className="text-xs font-bold text-white bg-red-500 rounded-full px-2 py-0.5 min-w-[20px] text-center animate-in zoom-in-50">
                      {unreadCount > 99 ? '99+' : unreadCount}
                    </span>
                  )}
                >
                  {notificationsLoading ? (
                    <div className="space-y-2">
                      {[0, 1, 2].map((i) => <div key={i} className="h-10 rounded-lg bg-gray-50 animate-pulse" />)}
                    </div>
                  ) : notifications.length === 0 ? (
                    <EmptyState icon={Bell} tone="gray" subtitle="لا توجد إشعارات حتى الآن" />
                  ) : (
                    <div className="space-y-1">
                      {notifications.slice(0, 5).map((n) => {
                        const meta = categoryMeta(n.category);
                        const Icon = meta.icon;
                        return (
                          <button
                            key={n.id}
                            onClick={() => handleNotificationClick(n)}
                            className={`w-full flex items-start gap-2.5 p-2 rounded-lg text-start transition-colors duration-150 hover:bg-gray-50 ${FOCUS_RING} ${!n.read_status ? 'bg-blue-50/60' : ''}`}
                            data-testid={`dashboard-notification-${n.id}`}
                          >
                            <div className={`flex-shrink-0 p-1.5 rounded-full ${meta.bg}`}>
                              <Icon className={`w-3.5 h-3.5 ${meta.color}`} />
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className={`text-xs truncate ${!n.read_status ? 'font-bold text-[#0A0A0A]' : 'text-gray-700'}`}>{n.title}</p>
                              <p className="text-[11px] text-gray-400 mt-0.5">{formatDistanceToNow(new Date(n.created_at), { addSuffix: true, locale: ar })}</p>
                            </div>
                            {!n.read_status && <span className="w-1.5 h-1.5 rounded-full bg-[#0033A0] flex-shrink-0 mt-1" />}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </Widget>
              </div>
            </div>

            {/* ---- Quick Actions ---- */}
            <div data-testid="employee-quick-actions">
              <h2 className="text-xl font-bold text-[#0A0A0A] mb-3 tracking-tight">إجراءات سريعة</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {[
                  { label: 'مهامي', icon: ListChecks, color: 'text-[#0033A0]', bg: 'bg-blue-50', path: '/employee/tasks' },
                  { label: 'الرسائل', icon: MessageSquare, color: 'text-teal-600', bg: 'bg-teal-50', path: '/employee/messages' },
                  { label: 'الحضور', icon: Clock, color: 'text-amber-600', bg: 'bg-amber-50', path: '/employee/attendance' },
                  { label: 'التقويم', icon: CalendarDays, color: 'text-indigo-600', bg: 'bg-indigo-50', path: '/employee/calendar' },
                  { label: 'التقارير', icon: FileText, color: 'text-emerald-600', bg: 'bg-emerald-50', path: '/employee/reports' },
                ].map((action) => (
                  <button
                    key={action.path}
                    type="button"
                    onClick={() => navigate(action.path)}
                    data-testid={`quick-action-${action.path.split('/').pop()}`}
                    className={`group flex flex-col items-center justify-center gap-2 p-4 ${CARD_SURFACE} hover:-translate-y-0.5 hover:shadow-md hover:border-[#0033A0]/20 transition-all duration-200 ${FOCUS_RING}`}
                  >
                    <div className={`p-2.5 rounded-full ${action.bg} transition-transform duration-200 group-hover:scale-110`}>
                      <action.icon className={`w-5 h-5 ${action.color}`} />
                    </div>
                    <span className="text-xs font-medium text-gray-700">{action.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Calendar Widgets - preserved from the previous dashboard */}
            {dashboard.calendar_widgets && (
              <Widget icon={CalendarDays} title="جدول اليوم" testId="employee-calendar-widgets">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2">أحداث اليوم</p>
                    {dashboard.calendar_widgets.today_events.length === 0 ? (
                      <p className="text-xs text-gray-400">لا توجد أحداث اليوم</p>
                    ) : dashboard.calendar_widgets.today_events.map((e) => (
                      <p key={e.id} className="text-xs text-gray-700 py-0.5">{e.start.slice(11, 16)} - {e.title}</p>
                    ))}
                  </div>
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><Timer className="w-3 h-3" /> الموعد القادم</p>
                    {dashboard.calendar_widgets.next_event ? (
                      <>
                        <p className="text-sm font-bold text-[#0A0A0A]">{dashboard.calendar_widgets.next_event.title}</p>
                        <p className="text-xs text-gray-500">{Math.floor(dashboard.calendar_widgets.next_event.seconds_remaining / 3600)} ساعة متبقية</p>
                      </>
                    ) : <p className="text-xs text-gray-400">لا يوجد</p>}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-4 border-t border-gray-100">
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2">اجتماعات قادمة</p>
                    {dashboard.calendar_widgets.upcoming_meetings.map((m) => (
                      <p key={m.id} className="text-xs text-gray-700 py-0.5 truncate">{m.title} - {m.start.slice(0, 10)}</p>
                    ))}
                    {dashboard.calendar_widgets.upcoming_meetings.length === 0 && <p className="text-xs text-gray-400">لا يوجد</p>}
                  </div>
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><Gift className="w-3 h-3" /> عطلات قادمة</p>
                    {dashboard.calendar_widgets.upcoming_holidays.map((h) => (
                      <p key={h.id} className="text-xs text-gray-700 py-0.5 truncate">{h.title} - {h.start.slice(0, 10)}</p>
                    ))}
                    {dashboard.calendar_widgets.upcoming_holidays.length === 0 && <p className="text-xs text-gray-400">لا يوجد</p>}
                  </div>
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><MailQuestion className="w-3 h-3" /> دعوات بلا رد</p>
                    <p className="text-2xl font-bold text-[#0A0A0A]">{dashboard.calendar_widgets.unanswered_invitations}</p>
                  </div>
                </div>
              </Widget>
            )}
          </>
        )}
      </div>
    </Layout>
  );
};

export default EmployeeDashboard;
