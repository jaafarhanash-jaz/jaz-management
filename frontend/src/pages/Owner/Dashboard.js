import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { DashboardCustomizePanel } from '@/components/DashboardCustomizePanel';
import CreateAnnouncementDialog from '@/components/CreateAnnouncementDialog';
import { useDashboardLayout, buildDefaultLayout } from '@/hooks/useDashboardLayout';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import {
  Users, CheckCircle, Clock, AlertCircle, Siren, Repeat, Zap, TrendingUp, LogOut, Percent,
  CalendarDays, Timer, Gift, MailQuestion, Plus, CalendarOff, Pencil, Mail, CreditCard,
  FileText, BarChart3, Trophy, Lightbulb, LayoutGrid, Inbox, ShieldAlert,
  UserPlus, CalendarClock, Megaphone, CalendarPlus, FileBarChart, GitBranch, ArrowRight,
} from 'lucide-react';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import CreateUrgentTaskDialog from '@/components/CreateUrgentTaskDialog';
import CreateCriticalTaskDialog from '@/components/CreateCriticalTaskDialog';

const STATUS_COLORS = { new: '#0033A0', seen: '#7C3AED', in_progress: '#FFB800', completed: '#00A36C', cancelled: '#A1A1AA', pending_review: '#FFB800', rejected: '#E11D48', overdue: '#E11D48' };
// Sunday=0..Saturday=6 - same convention as working_hours.working_days.
const WEEKDAY_LABELS = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

const SUBSCRIPTION_STATUS_META = {
  active: { label: 'نشط', color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  expired: { label: 'منتهي', color: 'text-red-700', bg: 'bg-red-50', border: 'border-red-200' },
  suspended: { label: 'موقوف', color: 'text-amber-700', bg: 'bg-amber-50', border: 'border-amber-200' },
};

const WORKFLOW_STEP_STATUS_META = {
  completed: { label: 'مكتملة', color: 'text-green-700', bg: 'bg-green-50', border: 'border-green-200' },
  in_progress: { label: 'قيد التنفيذ', color: 'text-yellow-700', bg: 'bg-yellow-50', border: 'border-yellow-200' },
  new: { label: 'قيد التنفيذ', color: 'text-yellow-700', bg: 'bg-yellow-50', border: 'border-yellow-200' },
  seen: { label: 'قيد التنفيذ', color: 'text-yellow-700', bg: 'bg-yellow-50', border: 'border-yellow-200' },
  pending_sequence: { label: 'بانتظار الدور', color: 'text-gray-500', bg: 'bg-gray-50', border: 'border-gray-200' },
  cancelled: { label: 'ملغاة', color: 'text-gray-500', bg: 'bg-gray-100', border: 'border-gray-300' },
  rejected: { label: 'مرفوضة', color: 'text-red-700', bg: 'bg-red-50', border: 'border-red-200' },
};

// Small=1/3 width, Medium=1/2 width, Large=full width, on a 12-column grid
// (collapses to a single column below the lg breakpoint regardless of
// size - resizing is a desktop-layout concern only).
const SIZE_TO_COLSPAN = { sm: 'lg:col-span-4', md: 'lg:col-span-6', lg: 'lg:col-span-12' };

// Card grids inside individual widgets reflow by available width
// (auto-fit), not by viewport breakpoint - so a widget set to Small still
// looks right instead of squeezing a 4-column grid into a narrow box.
const CARD_GRID = 'grid gap-4 grid-cols-[repeat(auto-fit,minmax(150px,1fr))]';
const CHART_GRID = 'grid gap-6 grid-cols-[repeat(auto-fit,minmax(320px,1fr))]';

const todayStr = () => new Date().toISOString().split('T')[0];

// Every dashboard section is a widget in this registry - key/title/icon are
// the only things the customization panel needs. Rendering itself lives in
// renderWidget() below, keyed off the same `key`. Adding a brand-new widget
// later means: one entry here, one case in renderWidget - nothing else in
// the customization system (persistence, drag-and-drop, show/hide) needs to
// change, which is what keeps this scalable.
const WIDGET_REGISTRY = [
  { key: 'stats', title: 'الإحصائيات العامة', icon: LayoutGrid },
  { key: 'messages', title: 'الرسائل', icon: Mail },
  { key: 'subscription', title: 'حالة الاشتراك', icon: CreditCard },
  { key: 'workflows', title: 'سير العمل التسلسلي', icon: GitBranch },
  { key: 'calendar', title: 'التقويم', icon: CalendarDays },
  { key: 'reports', title: 'آخر التقارير', icon: FileText },
  { key: 'analyticsStats', title: 'إحصائيات الأداء', icon: TrendingUp },
  { key: 'analyticsCharts', title: 'الرسوم البيانية', icon: BarChart3 },
  { key: 'employeeRanking', title: 'ترتيب الموظفين', icon: Trophy },
  { key: 'insights', title: 'ملاحظات', icon: Lightbulb },
];

// Advanced customization (Part 2) - which sub-elements each widget exposes
// for independent show/hide, beyond the whole-widget toggle already in
// WIDGET_REGISTRY. Keys are widget-specific and match what renderWidget()
// actually checks below; a key absent from a saved layout's `sections`
// object means "visible" (see DashboardWidgetLayout.sections on the
// backend), so this registry can grow over time without a migration.
const WIDGET_SECTIONS = {
  stats: [{ key: 'content', label: 'بطاقات الإحصائيات' }],
  messages: [
    { key: 'title', label: 'العنوان' },
    { key: 'actionButton', label: 'زر "عرض الكل"' },
    { key: 'content', label: 'المحتوى' },
  ],
  subscription: [
    { key: 'title', label: 'العنوان' },
    { key: 'content', label: 'المحتوى' },
  ],
  workflows: [
    { key: 'title', label: 'العنوان' },
    { key: 'actionButton', label: 'زر "عرض الكل"' },
    { key: 'content', label: 'المحتوى' },
  ],
  calendar: [
    { key: 'title', label: 'العنوان' },
    { key: 'actionButton', label: 'زر "عرض الكل"' },
    { key: 'content', label: 'المحتوى' },
  ],
  reports: [
    { key: 'title', label: 'العنوان' },
    { key: 'actionButton', label: 'زر "عرض الكل"' },
    { key: 'content', label: 'النشاط الأخير (قائمة التقارير)' },
  ],
  analyticsStats: [{ key: 'content', label: 'بطاقات الإحصائيات' }],
  analyticsCharts: [
    { key: 'chart_tasksOverTime', label: 'رسم: المهام المكتملة عبر الزمن' },
    { key: 'chart_taskDistribution', label: 'رسم: توزيع حالة المهام' },
    { key: 'chart_employeeRanking', label: 'رسم: ترتيب أداء الموظفين' },
    { key: 'chart_attendanceTrend', label: 'رسم: اتجاه الحضور' },
  ],
  employeeRanking: [
    { key: 'title', label: 'العنوان' },
    { key: 'actionButton', label: 'زر "عرض الكل"' },
    { key: 'content', label: 'قائمة الترتيب' },
  ],
  insights: [
    { key: 'title', label: 'العنوان' },
    { key: 'content', label: 'المحتوى' },
  ],
};

const EmptyState = ({ icon: Icon, text }) => (
  <div className="flex flex-col items-center justify-center py-10 text-center animate-in fade-in duration-300">
    <div className="p-3 rounded-full bg-gray-50 mb-3">
      <Icon className="w-6 h-6 text-gray-300" />
    </div>
    <p className="text-sm text-gray-400">{text}</p>
  </div>
);

const WidgetCard = ({ title, icon: Icon, children, testId, onViewAll, showTitle = true, showActionButton = true }) => (
  <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6 transition-shadow hover:shadow-md" data-testid={testId}>
    {(showTitle || (onViewAll && showActionButton)) && (
      <div className="flex items-center justify-between mb-4">
        {showTitle ? (
          <div className="flex items-center gap-2">
            {Icon && <Icon className="w-5 h-5 text-[#0033A0]" />}
            <h2 className="text-lg font-bold text-[#0A0A0A]">{title}</h2>
          </div>
        ) : <div />}
        {onViewAll && showActionButton && (
          <button
            type="button"
            onClick={onViewAll}
            data-testid={`${testId}-view-all`}
            className="text-xs font-medium text-[#0033A0] hover:underline flex items-center gap-1 transition-colors"
          >
            عرض الكل <ArrowRight className="w-3 h-3 flip-rtl" />
          </button>
        )}
      </div>
    )}
    {children}
  </Card>
);

// Productivity launcher (Part 5) - a single button that opens a popover
// grid of every quick-create action, instead of a growing row of
// individual buttons on the header.
const QuickActionsLauncher = ({ actions }) => {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="quick-actions-launcher-btn">
          <LayoutGrid className="w-4 h-4 me-2" /> إجراءات سريعة
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-2" data-testid="quick-actions-popover">
        <div className="grid grid-cols-2 gap-1">
          {actions.map((action) => {
            const Icon = action.icon;
            return (
              <button
                key={action.key}
                type="button"
                data-testid={`quick-action-${action.key}`}
                onClick={() => { setOpen(false); action.onClick(); }}
                className="flex flex-col items-start gap-2 p-3 rounded-sm hover:bg-gray-50 text-start transition-colors"
              >
                <div className={`p-2 rounded-sm ${action.bg}`}>
                  <Icon className={`w-4 h-4 ${action.color}`} />
                </div>
                <span className="text-xs font-medium text-[#0A0A0A]">{action.label}</span>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
};

const OwnerDashboard = ({ onLogout, language, setLanguage }) => {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [messagesUnread, setMessagesUnread] = useState(0);
  const [subscription, setSubscription] = useState(null);
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [urgentDialogOpen, setUrgentDialogOpen] = useState(false);
  const [criticalDialogOpen, setCriticalDialogOpen] = useState(false);
  const [announcementDialogOpen, setAnnouncementDialogOpen] = useState(false);
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [draftLayout, setDraftLayout] = useState(null);

  const { layout, save: saveLayout, saving: savingLayout } = useDashboardLayout(WIDGET_REGISTRY);

  const openCustomize = () => {
    setDraftLayout(layout ? layout.map((w) => ({ ...w })) : buildDefaultLayout(WIDGET_REGISTRY));
    setCustomizeOpen(true);
  };
  const cancelCustomize = () => {
    setCustomizeOpen(false);
    setDraftLayout(null);
  };
  const saveCustomize = async () => {
    const ok = await saveLayout(draftLayout);
    if (ok) {
      setCustomizeOpen(false);
      setDraftLayout(null);
    }
  };
  const restoreDefaultLayout = () => setDraftLayout(buildDefaultLayout(WIDGET_REGISTRY));

  // While customizing, the dashboard renders the live (unsaved) draft, not
  // the last-saved layout - every show/hide/reorder/resize edit in the
  // panel is reflected here immediately, which is the live preview itself,
  // not a separate mechanism.
  const activeLayout = customizeOpen ? draftLayout : layout;

  useEffect(() => {
    fetchAll();
    const interval = setInterval(() => fetchAll(true), 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAll = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [dashRes, analyticsRes, empRes, messagesRes, subRes, workflowsRes] = await Promise.all([
        api.get('/owner/dashboard'),
        api.get('/owner/analytics'),
        api.get('/owner/employees'),
        api.get('/messages/inbox?unread_only=true&page_size=1'),
        api.get('/owner/subscription'),
        api.get('/owner/tasks/workflows'),
      ]);
      setDashboard(dashRes.data);
      setAnalytics(analyticsRes.data);
      setEmployees(empRes.data);
      setMessagesUnread(messagesRes.data.total ?? 0);
      setSubscription(subRes.data);
      setWorkflows(workflowsRes.data);
    } catch (error) {
      // A failed fetch (transient network error, backend momentarily
      // unreachable, etc.) must not leave the page in an inconsistent
      // "not loading, but no data" state - the render below only enters
      // its data-dependent branch once `dashboard` is actually populated,
      // so this can safely leave state untouched (or null on first load)
      // without risking a crash. The 10s silent poll below will naturally
      // recover once the backend is reachable again.
      console.error('Error fetching dashboard:', error);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  // Part 1 - every widget/stat navigates to the right page with the right
  // filter already applied, via real query params the target page reads
  // on mount (see Owner/Attendance.js, Owner/Tasks.js). No fake "coming
  // soon" links - every card below goes somewhere real.
  const goToAttendance = (params = {}) => navigate(`/company-owner/attendance?${new URLSearchParams({ tab: 'records', date_from: todayStr(), date_to: todayStr(), ...params }).toString()}`);
  const goToTasks = (params = {}) => navigate(Object.keys(params).length ? `/company-owner/tasks?${new URLSearchParams(params).toString()}` : '/company-owner/tasks');

  const statCards = dashboard ? [
    { title: t('totalEmployees', language), value: dashboard.total_employees, icon: Users, color: 'text-blue-600', bg: 'bg-blue-50', onClick: () => navigate('/company-owner/employees') },
    { title: t('presentToday', language), value: dashboard.present_today, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50', onClick: () => goToAttendance({ status: 'present' }) },
    { title: t('lateToday', language), value: dashboard.late_today, icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50', onClick: () => goToAttendance({ status: 'late' }) },
    { title: t('absentToday', language), value: dashboard.absent_today, icon: AlertCircle, color: 'text-red-600', bg: 'bg-red-50', onClick: () => goToAttendance() },
    { title: 'انصرفوا اليوم', value: dashboard.checked_out_today ?? 0, icon: LogOut, color: 'text-slate-600', bg: 'bg-slate-50', onClick: () => goToAttendance({ status: 'checked_out' }) },
    { title: 'نسبة الحضور اليوم', value: `${dashboard.attendance_percentage ?? 0}%`, icon: Percent, color: 'text-emerald-600', bg: 'bg-emerald-50', onClick: () => goToAttendance({ tab: 'analytics' }) },
    { title: t('openTasks', language), value: dashboard.open_tasks, icon: CheckCircle, color: 'text-purple-600', bg: 'bg-purple-50', onClick: () => goToTasks() },
    { title: t('completedTasks', language), value: dashboard.completed_tasks, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50', onClick: () => goToTasks() },
    { title: t('overdueTasks', language), value: dashboard.overdue_tasks, icon: AlertCircle, color: 'text-red-600', bg: 'bg-red-50', onClick: () => goToTasks({ view: 'overdue' }) },
    { title: 'المهام اليومية النشطة', value: dashboard.active_daily_tasks, icon: Repeat, color: 'text-indigo-600', bg: 'bg-indigo-50', onClick: () => goToTasks() },
    { title: 'المهام الفورية المعلقة', value: dashboard.pending_urgent_tasks, icon: Zap, color: 'text-red-600', bg: 'bg-red-50', onClick: () => goToTasks({ view: 'urgent' }) },
    { title: 'المهام الفورية المكتملة', value: dashboard.completed_urgent_tasks, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50', onClick: () => goToTasks({ view: 'urgent' }) },
  ] : [];

  const quickActions = [
    { key: 'employee', label: 'إضافة موظف', icon: UserPlus, color: 'text-blue-600', bg: 'bg-blue-50', onClick: () => navigate('/company-owner/employees') },
    { key: 'task', label: 'إضافة مهمة', icon: Plus, color: 'text-[#0033A0]', bg: 'bg-blue-50', onClick: () => navigate('/company-owner/tasks') },
    { key: 'scheduled_task', label: 'مهمة مجدولة', icon: CalendarClock, color: 'text-indigo-600', bg: 'bg-indigo-50', onClick: () => navigate('/company-owner/tasks') },
    { key: 'urgent_task', label: 'مهمة فورية', icon: Zap, color: 'text-orange-600', bg: 'bg-orange-50', onClick: () => setUrgentDialogOpen(true) },
    { key: 'critical_task', label: 'مهمة عاجلة', icon: Siren, color: 'text-red-600', bg: 'bg-red-50', onClick: () => setCriticalDialogOpen(true) },
    { key: 'announcement', label: 'إعلان جديد', icon: Megaphone, color: 'text-pink-600', bg: 'bg-pink-50', onClick: () => setAnnouncementDialogOpen(true) },
    { key: 'meeting', label: 'اجتماع جديد', icon: CalendarPlus, color: 'text-teal-600', bg: 'bg-teal-50', onClick: () => navigate('/company-owner/calendar') },
    { key: 'report', label: 'إنشاء تقرير', icon: FileBarChart, color: 'text-emerald-600', bg: 'bg-emerald-50', onClick: () => navigate('/company-owner/reports') },
  ];

  const renderWidget = (key, sections = {}) => {
    const show = (s) => sections[s] !== false;
    switch (key) {
      case 'stats':
        if (!show('content')) return null;
        return (
          <div className={CARD_GRID} data-testid="widget-stats">
            {statCards.map((card, index) => {
              const Icon = card.icon;
              return (
                <button
                  key={index}
                  type="button"
                  onClick={card.onClick}
                  data-testid={`owner-stat-card-${index}`}
                  className="text-start w-full"
                >
                  <Card className="stat-card bg-white border border-gray-200 rounded-md shadow-sm p-6 transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 hover:border-[#0033A0]/30 cursor-pointer">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="text-sm text-gray-600 mb-1">{card.title}</p>
                        <p className="text-3xl font-bold text-[#0A0A0A]">{card.value}</p>
                      </div>
                      <div className={`p-3 rounded-sm ${card.bg}`}>
                        <Icon className={`w-6 h-6 ${card.color}`} />
                      </div>
                    </div>
                  </Card>
                </button>
              );
            })}
          </div>
        );

      case 'messages':
        return (
          <WidgetCard
            title="الرسائل" icon={Mail} testId="widget-messages"
            onViewAll={() => navigate('/company-owner/communication-center')}
            showTitle={show('title')} showActionButton={show('actionButton')}
          >
            {show('content') && (messagesUnread > 0 ? (
              <button
                type="button"
                onClick={() => navigate('/company-owner/communication-center')}
                className="w-full flex items-center justify-between text-start rounded-md p-2 -m-2 transition-colors hover:bg-gray-50"
                data-testid="widget-messages-cta"
              >
                <div className="flex items-center gap-3">
                  <div className="p-3 rounded-sm bg-blue-50">
                    <Inbox className="w-6 h-6 text-blue-600" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-[#0A0A0A]">{messagesUnread}</p>
                    <p className="text-sm text-gray-500">رسالة غير مقروءة</p>
                  </div>
                </div>
              </button>
            ) : (
              <EmptyState icon={Mail} text="لا توجد رسائل غير مقروءة" />
            ))}
          </WidgetCard>
        );

      case 'subscription': {
        const meta = subscription ? SUBSCRIPTION_STATUS_META[subscription.subscription_status] : null;
        const daysRemaining = subscription?.subscription_end_date
          ? Math.max(0, Math.ceil((new Date(subscription.subscription_end_date) - new Date()) / 86400000))
          : null;
        return (
          <WidgetCard title="حالة الاشتراك" icon={CreditCard} testId="widget-subscription" showTitle={show('title')}>
            {show('content') && (subscription?.current_plan ? (
              <button
                type="button"
                onClick={() => navigate('/company-owner/subscription')}
                className={`w-full flex items-center justify-between p-4 rounded-md border text-start transition-colors ${meta?.border || 'border-gray-200'} ${meta?.bg || 'bg-gray-50'} hover:brightness-95`}
                data-testid="widget-subscription-cta"
              >
                <div>
                  <p className="font-bold text-[#0A0A0A]">{subscription.current_plan.name}</p>
                  {daysRemaining !== null && (
                    <p className="text-xs text-gray-500 mt-1">
                      {daysRemaining > 0 ? `${daysRemaining} يوم متبقٍ` : 'انتهت مدة الاشتراك'}
                    </p>
                  )}
                </div>
                <span className={`text-xs font-bold px-3 py-1 rounded-full ${meta?.color || 'text-gray-600'} bg-white`}>
                  {meta?.label || subscription.subscription_status}
                </span>
              </button>
            ) : (
              <EmptyState icon={CreditCard} text="لا يوجد اشتراك نشط" />
            ))}
          </WidgetCard>
        );
      }

      case 'workflows':
        return (
          <WidgetCard
            title="سير العمل التسلسلي" icon={GitBranch} testId="widget-workflows"
            onViewAll={() => navigate('/company-owner/tasks')}
            showTitle={show('title')} showActionButton={show('actionButton')}
          >
            {show('content') && (workflows.length > 0 ? (
              <div className="space-y-4">
                {workflows.map((wf) => (
                  <div key={wf.batch_id} className="p-4 border border-gray-100 rounded-sm" data-testid={`workflow-${wf.batch_id}`}>
                    <p className="font-medium text-[#0A0A0A] mb-3">{wf.title}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      {wf.steps.map((step, i) => {
                        const meta = WORKFLOW_STEP_STATUS_META[step.status] || WORKFLOW_STEP_STATUS_META.pending_sequence;
                        return (
                          <div key={step.task_id} className="flex items-center gap-2">
                            <div className={`px-3 py-1.5 rounded-md border text-xs ${meta.bg} ${meta.border}`}>
                              <p className="font-medium text-[#0A0A0A]">{step.employee_name}</p>
                              <p className={meta.color}>{meta.label}</p>
                            </div>
                            {i < wf.steps.length - 1 && <ArrowRight className="w-3.5 h-3.5 text-gray-300 flip-rtl flex-shrink-0" />}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon={GitBranch} text="لا توجد سير عمل تسلسلي نشطة" />
            ))}
          </WidgetCard>
        );

      case 'calendar':
        return dashboard?.calendar_widgets ? (
          <WidgetCard
            title="التقويم" icon={CalendarDays} testId="widget-calendar"
            onViewAll={() => navigate('/company-owner/calendar')}
            showTitle={show('title')} showActionButton={show('actionButton')}
          >
            {show('content') && (<>
            <div className={CARD_GRID}>
              <Card className="p-4 border border-gray-100 rounded-sm">
                <p className="text-xs text-gray-500 mb-1">أحداث اليوم</p>
                <p className="text-2xl font-bold text-[#0A0A0A]">{dashboard.calendar_widgets.today_events.length}</p>
              </Card>
              <Card className="p-4 border border-gray-100 rounded-sm">
                <p className="text-xs text-gray-500 mb-1">أحداث الغد</p>
                <p className="text-2xl font-bold text-[#0A0A0A]">{dashboard.calendar_widgets.tomorrow_events.length}</p>
              </Card>
              <Card className="p-4 border border-gray-100 rounded-sm">
                <p className="text-xs text-gray-500 mb-1 flex items-center gap-1"><Timer className="w-3 h-3" /> الموعد القادم</p>
                {dashboard.calendar_widgets.next_event ? (
                  <>
                    <p className="text-sm font-bold text-[#0A0A0A] truncate">{dashboard.calendar_widgets.next_event.title}</p>
                    <p className="text-xs text-gray-500">{Math.floor(dashboard.calendar_widgets.next_event.seconds_remaining / 3600)} ساعة متبقية</p>
                  </>
                ) : <p className="text-sm text-gray-400">لا يوجد</p>}
              </Card>
              <Card className="p-4 border border-gray-100 rounded-sm">
                <p className="text-xs text-gray-500 mb-1 flex items-center gap-1"><MailQuestion className="w-3 h-3" /> بانتظار الرد</p>
                <p className="text-2xl font-bold text-[#0A0A0A]">{dashboard.calendar_widgets.events_requiring_response}</p>
              </Card>
            </div>
            <div className={`${CARD_GRID} mt-4`}>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-2">اجتماعات قادمة</p>
                {dashboard.calendar_widgets.upcoming_meetings.map((m) => (
                  <p key={m.id} className="text-xs text-gray-700 py-0.5 truncate">{m.title} - {m.start.slice(0, 10)}</p>
                ))}
                {dashboard.calendar_widgets.upcoming_meetings.length === 0 && <p className="text-xs text-gray-400">لا يوجد</p>}
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><Gift className="w-3 h-3" /> عطلات هذا الأسبوع</p>
                {dashboard.calendar_widgets.upcoming_holidays.map((h) => (
                  <p key={h.id} className="text-xs text-gray-700 py-0.5 truncate">{h.title} - {h.start.slice(0, 10)}</p>
                ))}
                {dashboard.calendar_widgets.upcoming_holidays.length === 0 && <p className="text-xs text-gray-400">لا يوجد</p>}
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-2 flex items-center gap-1"><CalendarOff className="w-3 h-3" /> العطلة الأسبوعية</p>
                {dashboard.weekly_holiday_days && dashboard.weekly_holiday_days.length > 0 ? (
                  <p className="text-xs text-gray-700 py-0.5">{dashboard.weekly_holiday_days.map((d) => WEEKDAY_LABELS[d]).join('، ')}</p>
                ) : <p className="text-xs text-gray-400">لا يوجد</p>}
              </div>
              <div>
                <p className="text-xs font-medium text-gray-500 mb-2">مواعيد عالية الأولوية</p>
                {dashboard.calendar_widgets.high_priority_events?.map((h) => (
                  <p key={h.id} className="text-xs text-red-600 py-0.5 truncate">{h.title} - {h.start.slice(0, 10)}</p>
                ))}
                {(!dashboard.calendar_widgets.high_priority_events || dashboard.calendar_widgets.high_priority_events.length === 0) && <p className="text-xs text-gray-400">لا يوجد</p>}
              </div>
            </div>
            </>)}
          </WidgetCard>
        ) : null;

      case 'reports':
        return (
          <WidgetCard
            title="آخر التقارير" icon={FileText} testId="widget-reports"
            onViewAll={() => navigate('/company-owner/reports')}
            showTitle={show('title')} showActionButton={show('actionButton')}
          >
            {show('content') && (dashboard?.recent_reports && dashboard.recent_reports.length > 0 ? (
              <div className="space-y-3">
                {dashboard.recent_reports.map((report) => (
                  <button
                    key={report.id}
                    type="button"
                    onClick={() => navigate('/company-owner/reports')}
                    className="w-full p-4 border border-gray-100 rounded-sm hover:bg-gray-50 transition-colors text-start"
                    data-testid={`report-${report.id}`}
                  >
                    <h3 className="font-medium text-[#0A0A0A]">{report.title}</h3>
                    <p className="text-sm text-gray-600 mt-1">{report.description}</p>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState icon={FileText} text="لا توجد تقارير بعد" />
            ))}
          </WidgetCard>
        );

      case 'analyticsStats':
        if (!show('content')) return null;
        return analytics ? (
          <div className={CARD_GRID} data-testid="widget-analyticsStats">
            {[
              { label: 'إجمالي المهام', value: analytics.total_tasks },
              { label: 'المهام المكتملة', value: analytics.completed_tasks },
              { label: 'المهام المعلقة', value: analytics.pending_tasks },
              { label: 'المهام الفورية', value: analytics.urgent_tasks },
              { label: 'متوسط وقت الإنجاز (دقيقة)', value: analytics.avg_completion_time_minutes ?? '-' },
              { label: 'متوسط وقت الاستجابة (دقيقة)', value: analytics.avg_response_time_minutes ?? '-' },
              { label: 'مكتملة اليوم', value: analytics.tasks_completed_today },
              { label: 'مكتملة هذا الأسبوع', value: analytics.tasks_completed_week },
              { label: 'مكتملة هذا الشهر', value: analytics.tasks_completed_month },
              { label: 'نسبة الحضور', value: `${analytics.attendance_rate}%` },
              { label: 'عدد التأخيرات', value: analytics.late_attendance_count },
            ].map((stat, i) => (
              <Card key={i} className="p-4 bg-white border border-gray-200 rounded-md transition-shadow hover:shadow-sm" data-testid={`analytics-stat-${i}`}>
                <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{stat.label}</p>
                <p className="text-2xl font-bold text-[#0A0A0A]">{stat.value}</p>
              </Card>
            ))}
          </div>
        ) : null;

      case 'analyticsCharts':
        return analytics ? (
          <div className={CHART_GRID} data-testid="widget-analyticsCharts">
            {show('chart_tasksOverTime') && (
            <Card className="p-6 bg-white border border-gray-200 rounded-md">
              <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">المهام المكتملة عبر الزمن</h3>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={analytics.charts.tasks_completed_over_time}>
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Line type="monotone" dataKey="count" stroke="#0033A0" strokeWidth={2} animationDuration={400} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            )}

            {show('chart_taskDistribution') && (
            <Card className="p-6 bg-white border border-gray-200 rounded-md">
              <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">توزيع حالة المهام</h3>
              {analytics.charts.task_distribution.length > 0 ? (
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie data={analytics.charts.task_distribution} dataKey="count" nameKey="status" cx="50%" cy="50%" outerRadius={90} label animationDuration={400}>
                      {analytics.charts.task_distribution.map((entry, i) => (
                        <Cell key={i} fill={STATUS_COLORS[entry.status] || '#A1A1AA'} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : <EmptyState icon={BarChart3} text="لا توجد بيانات مهام بعد" />}
            </Card>
            )}

            {show('chart_employeeRanking') && (
            <Card className="p-6 bg-white border border-gray-200 rounded-md">
              <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">ترتيب أداء الموظفين</h3>
              {analytics.charts.employee_performance_ranking.length > 0 ? (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={analytics.charts.employee_performance_ranking}>
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                    <YAxis domain={[0, 100]} />
                    <Tooltip />
                    <Bar dataKey="score" fill="#0033A0" radius={[4, 4, 0, 0]} animationDuration={400} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <EmptyState icon={Trophy} text="لا يوجد موظفون بعد" />}
            </Card>
            )}

            {show('chart_attendanceTrend') && (
            <Card className="p-6 bg-white border border-gray-200 rounded-md">
              <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">اتجاه الحضور (آخر 7 أيام)</h3>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={analytics.charts.attendance_trend}>
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 100]} />
                  <Tooltip />
                  <Line type="monotone" dataKey="rate" stroke="#00A36C" strokeWidth={2} animationDuration={400} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            )}
          </div>
        ) : null;

      case 'employeeRanking':
        return (
          <WidgetCard
            title="ترتيب الموظفين" icon={Trophy} testId="employee-ranking"
            onViewAll={() => navigate('/company-owner/employees')}
            showTitle={show('title')} showActionButton={show('actionButton')}
          >
            {show('content') && (analytics?.employee_ranking && analytics.employee_ranking.length > 0 ? (
              <div className="space-y-2">
                {analytics.employee_ranking.map((e) => (
                  <div key={e.employee_id} className="flex items-center justify-between p-3 border border-gray-100 rounded-sm transition-colors hover:bg-gray-50" data-testid={`ranking-${e.employee_id}`}>
                    <div className="flex items-center gap-3">
                      <span className="w-6 h-6 flex items-center justify-center bg-[#0033A0] text-white text-xs font-bold rounded-full">{e.rank}</span>
                      <div>
                        <p className="font-medium text-[#0A0A0A]">{e.name}</p>
                        <p className="text-xs text-gray-500">{e.rating}</p>
                      </div>
                    </div>
                    <p className="text-xl font-bold text-[#0033A0]">{e.performance_score}%</p>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon={Trophy} text="لا يوجد موظفون بعد" />
            ))}
          </WidgetCard>
        );

      case 'insights':
        return (
          <WidgetCard title="ملاحظات" icon={Lightbulb} testId="insights-section" showTitle={show('title')}>
            {show('content') && (analytics?.insights && analytics.insights.length > 0 ? (
              <ul className="space-y-2">
                {analytics.insights.map((insight, i) => (
                  <li key={i} className="text-sm text-gray-700 flex items-start gap-2" data-testid={`insight-${i}`}>
                    <span className="text-[#0033A0]">•</span> {insight}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={Lightbulb} text="لا توجد ملاحظات بعد" />
            ))}
          </WidgetCard>
        );

      default:
        return null;
    }
  };

  const visibleOrderedWidgets = activeLayout
    ? [...activeLayout].filter((w) => w.visible).sort((a, b) => a.order - b.order)
    : buildDefaultLayout(WIDGET_REGISTRY);

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-start flex-wrap gap-4">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="owner-dashboard-title">
              {t('dashboard', language)}
            </h1>
            <p className="text-gray-600 mt-2">مرحباً بك في لوحة إدارة شركتك</p>
          </div>
          <div className="flex items-center gap-2">
            <QuickActionsLauncher actions={quickActions} />
            <button
              type="button"
              onClick={openCustomize}
              data-testid="dashboard-customize-btn"
              aria-label="تخصيص لوحة التحكم"
              className="flex items-center justify-center w-10 h-10 rounded-sm border border-gray-200 bg-white hover:bg-gray-50 hover:border-[#0033A0]/30 hover:text-[#0033A0] hover:-translate-y-0.5 text-gray-600 transition-all duration-200"
            >
              <Pencil className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Company Holiday banner - additive, only rendered when today is an
            active company holiday or a configured weekly holiday. */}
        {dashboard?.today_is_holiday && (
          <Card className="p-4 bg-emerald-50 border border-emerald-200 rounded-md flex items-center gap-3 animate-in fade-in slide-in-from-top-1 duration-300" data-testid="today-holiday-banner">
            <Gift className="w-6 h-6 text-emerald-600 flex-shrink-0" />
            <p className="text-emerald-800 font-medium">
              {dashboard.today_holiday_type === 'weekly_holiday'
                ? 'اليوم عطلة أسبوعية'
                : `اليوم عطلة رسمية للشركة${dashboard.today_holiday_title ? `: ${dashboard.today_holiday_title}` : ''}`}
            </p>
          </Card>
        )}

        {subscription?.subscription_status && subscription.subscription_status !== 'active' && (
          <Card className="p-4 bg-red-50 border border-red-200 rounded-md flex items-center gap-3 animate-in fade-in slide-in-from-top-1 duration-300" data-testid="subscription-warning-banner">
            <ShieldAlert className="w-6 h-6 text-red-600 flex-shrink-0" />
            <p className="text-red-800 font-medium">
              اشتراك شركتك {SUBSCRIPTION_STATUS_META[subscription.subscription_status]?.label || subscription.subscription_status} - جدد الاشتراك لتجنب انقطاع الخدمة.
            </p>
          </Card>
        )}

        {loading ? (
          // Skeleton mirrors the real widget grid's varied column spans
          // (stats full-width, then paired half-width widgets) instead of
          // identical bars, with a tasteful stagger so it reads as a single
          // coordinated shimmer rather than a flat block.
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6" data-testid="dashboard-loading-skeleton">
            {[
              { span: 'lg:col-span-12', h: 'h-32' },
              { span: 'lg:col-span-6', h: 'h-48' },
              { span: 'lg:col-span-6', h: 'h-48' },
              { span: 'lg:col-span-12', h: 'h-56' },
            ].map((s, i) => (
              <div key={i} className={s.span}>
                <div
                  className={`${s.h} rounded-md bg-gray-100 animate-pulse`}
                  style={{ animationDelay: `${i * 75}ms` }}
                />
              </div>
            ))}
          </div>
        ) : !dashboard ? (
          // The initial fetch failed (network error / backend momentarily
          // unreachable) - render an explicit, recoverable state instead of
          // falling through into content that assumes `dashboard` exists.
          // The 10s silent poll above will replace this automatically once
          // a request succeeds; the button below lets the owner force it.
          <div className="text-center py-12 text-gray-500" data-testid="owner-dashboard-error">
            <p className="mb-4">تعذر تحميل بيانات لوحة التحكم. تحقق من الاتصال وحاول مرة أخرى.</p>
            <Button onClick={() => fetchAll()} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="dashboard-retry-btn">
              إعادة المحاولة
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {visibleOrderedWidgets.map(({ key, size, sections }) => (
              <div key={key} className={`${SIZE_TO_COLSPAN[size || 'lg']} animate-in fade-in duration-300`}>{renderWidget(key, sections || {})}</div>
            ))}
          </div>
        )}
      </div>

      <CreateUrgentTaskDialog
        open={urgentDialogOpen}
        onOpenChange={setUrgentDialogOpen}
        employees={employees}
        onCreated={() => fetchAll(true)}
      />
      <CreateCriticalTaskDialog
        open={criticalDialogOpen}
        onOpenChange={setCriticalDialogOpen}
        employees={employees}
        onCreated={() => fetchAll(true)}
      />
      <CreateAnnouncementDialog
        open={announcementDialogOpen}
        onOpenChange={setAnnouncementDialogOpen}
        onCreated={() => fetchAll(true)}
      />
      <DashboardCustomizePanel
        open={customizeOpen}
        registry={WIDGET_REGISTRY}
        sectionsRegistry={WIDGET_SECTIONS}
        layout={draftLayout}
        onChange={setDraftLayout}
        onSave={saveCustomize}
        onCancel={cancelCustomize}
        onRestoreDefault={restoreDefaultLayout}
        saving={savingLayout}
        language={language}
      />
    </Layout>
  );
};

export default OwnerDashboard;
