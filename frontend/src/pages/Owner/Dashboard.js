import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Users, CheckCircle, Clock, AlertCircle, Siren, Repeat, Zap, TrendingUp, LogOut, Percent, CalendarDays, Timer, Gift, MailQuestion, Plus, CalendarOff } from 'lucide-react';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import CreateUrgentTaskDialog from '@/components/CreateUrgentTaskDialog';
import CreateCriticalTaskDialog from '@/components/CreateCriticalTaskDialog';

const STATUS_COLORS = { new: '#0033A0', seen: '#7C3AED', in_progress: '#FFB800', completed: '#00A36C', cancelled: '#A1A1AA', pending_review: '#FFB800', rejected: '#E11D48', overdue: '#E11D48' };
// Sunday=0..Saturday=6 - same convention as working_hours.working_days.
const WEEKDAY_LABELS = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

const OwnerDashboard = ({ onLogout, language, setLanguage }) => {
  const [dashboard, setDashboard] = useState(null);
  const [analytics, setAnalytics] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [urgentDialogOpen, setUrgentDialogOpen] = useState(false);
  const [criticalDialogOpen, setCriticalDialogOpen] = useState(false);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(() => fetchAll(true), 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAll = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [dashRes, analyticsRes, empRes] = await Promise.all([
        api.get('/owner/dashboard'),
        api.get('/owner/analytics'),
        api.get('/owner/employees')
      ]);
      setDashboard(dashRes.data);
      setAnalytics(analyticsRes.data);
      setEmployees(empRes.data);
    } catch (error) {
      // A failed fetch (transient network error, backend momentarily
      // unreachable, etc.) must not leave the page in an inconsistent
      // "not loading, but no data" state - the render below only enters
      // its data-dependent branch once `dashboard` is actually populated,
      // so this can safely leave dashboard/analytics/employees untouched
      // (or null on first load) without risking a crash. The 10s silent
      // poll below will naturally recover once the backend is reachable
      // again.
      console.error('Error fetching dashboard:', error);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const statCards = dashboard ? [
    { title: t('totalEmployees', language), value: dashboard.total_employees, icon: Users, color: 'text-blue-600', bg: 'bg-blue-50' },
    { title: t('presentToday', language), value: dashboard.present_today, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50' },
    { title: t('lateToday', language), value: dashboard.late_today, icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50' },
    { title: t('absentToday', language), value: dashboard.absent_today, icon: AlertCircle, color: 'text-red-600', bg: 'bg-red-50' },
    { title: 'انصرفوا اليوم', value: dashboard.checked_out_today ?? 0, icon: LogOut, color: 'text-slate-600', bg: 'bg-slate-50' },
    { title: 'نسبة الحضور اليوم', value: `${dashboard.attendance_percentage ?? 0}%`, icon: Percent, color: 'text-emerald-600', bg: 'bg-emerald-50' },
    { title: t('openTasks', language), value: dashboard.open_tasks, icon: CheckCircle, color: 'text-purple-600', bg: 'bg-purple-50' },
    { title: t('completedTasks', language), value: dashboard.completed_tasks, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50' },
    { title: t('overdueTasks', language), value: dashboard.overdue_tasks, icon: AlertCircle, color: 'text-red-600', bg: 'bg-red-50' },
    { title: 'المهام اليومية النشطة', value: dashboard.active_daily_tasks, icon: Repeat, color: 'text-indigo-600', bg: 'bg-indigo-50' },
    { title: 'المهام الفورية المعلقة', value: dashboard.pending_urgent_tasks, icon: Zap, color: 'text-red-600', bg: 'bg-red-50' },
    { title: 'المهام الفورية المكتملة', value: dashboard.completed_urgent_tasks, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50' },
  ] : [];

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
            <Button
              onClick={() => setUrgentDialogOpen(true)}
              className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm"
              data-testid="emergency-task-btn"
            >
              <Plus className="w-4 h-4 me-2" /> إضافة مهمة
            </Button>
            <Button
              onClick={() => setCriticalDialogOpen(true)}
              className="bg-red-700 hover:bg-red-800 text-white rounded-sm"
              data-testid="critical-task-btn"
            >
              <Siren className="w-4 h-4 me-2" /> إضافة مهمة عاجلة
            </Button>
          </div>
        </div>

        {/* Company Holiday banner - additive, only rendered when today is an
            active company holiday or a configured weekly holiday. */}
        {dashboard?.today_is_holiday && (
          <Card className="p-4 bg-emerald-50 border border-emerald-200 rounded-md flex items-center gap-3" data-testid="today-holiday-banner">
            <Gift className="w-6 h-6 text-emerald-600 flex-shrink-0" />
            <p className="text-emerald-800 font-medium">
              {dashboard.today_holiday_type === 'weekly_holiday'
                ? 'اليوم عطلة أسبوعية'
                : `اليوم عطلة رسمية للشركة${dashboard.today_holiday_title ? `: ${dashboard.today_holiday_title}` : ''}`}
            </p>
          </Card>
        )}

        {loading ? (
          <div className="text-center py-12">Loading...</div>
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
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {statCards.map((card, index) => {
                const Icon = card.icon;
                return (
                  <Card key={index} data-testid={`owner-stat-card-${index}`} className="stat-card bg-white border border-gray-200 rounded-md shadow-sm p-6">
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
                );
              })}
            </div>

            {/* Calendar Widgets - additive section, existing dashboard content above is unchanged */}
            {dashboard.calendar_widgets && (
              <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6" data-testid="owner-calendar-widgets">
                <div className="flex items-center gap-2 mb-4">
                  <CalendarDays className="w-5 h-5 text-[#0033A0]" />
                  <h2 className="text-xl font-bold text-[#0A0A0A]">التقويم</h2>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
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
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
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
              </Card>
            )}

            {dashboard.recent_reports && dashboard.recent_reports.length > 0 && (
              <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6">
                <h2 className="text-xl font-bold text-[#0A0A0A] mb-4">آخر التقارير</h2>
                <div className="space-y-3">
                  {dashboard.recent_reports.map((report) => (
                    <div key={report.id} className="p-4 border border-gray-100 rounded-sm hover:bg-gray-50 transition-colors" data-testid={`report-${report.id}`}>
                      <h3 className="font-medium text-[#0A0A0A]">{report.title}</h3>
                      <p className="text-sm text-gray-600 mt-1">{report.description}</p>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Analytics Section */}
            {analytics && (
              <div className="space-y-6" data-testid="analytics-section">
                <div className="flex items-center gap-2 pt-4">
                  <TrendingUp className="w-6 h-6 text-[#0033A0]" />
                  <h2 className="text-2xl font-bold text-[#0A0A0A]">تحليلات الأداء</h2>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
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
                    <Card key={i} className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`analytics-stat-${i}`}>
                      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{stat.label}</p>
                      <p className="text-2xl font-bold text-[#0A0A0A]">{stat.value}</p>
                    </Card>
                  ))}
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">المهام المكتملة عبر الزمن</h3>
                    <ResponsiveContainer width="100%" height={260}>
                      <LineChart data={analytics.charts.tasks_completed_over_time}>
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                        <YAxis allowDecimals={false} />
                        <Tooltip />
                        <Line type="monotone" dataKey="count" stroke="#0033A0" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Card>

                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">توزيع حالة المهام</h3>
                    {analytics.charts.task_distribution.length > 0 ? (
                      <ResponsiveContainer width="100%" height={260}>
                        <PieChart>
                          <Pie data={analytics.charts.task_distribution} dataKey="count" nameKey="status" cx="50%" cy="50%" outerRadius={90} label>
                            {analytics.charts.task_distribution.map((entry, i) => (
                              <Cell key={i} fill={STATUS_COLORS[entry.status] || '#A1A1AA'} />
                            ))}
                          </Pie>
                          <Tooltip />
                          <Legend />
                        </PieChart>
                      </ResponsiveContainer>
                    ) : <p className="text-center text-gray-500 py-12">لا توجد بيانات</p>}
                  </Card>

                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">ترتيب أداء الموظفين</h3>
                    {analytics.charts.employee_performance_ranking.length > 0 ? (
                      <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={analytics.charts.employee_performance_ranking}>
                          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                          <YAxis domain={[0, 100]} />
                          <Tooltip />
                          <Bar dataKey="score" fill="#0033A0" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    ) : <p className="text-center text-gray-500 py-12">لا توجد بيانات</p>}
                  </Card>

                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">اتجاه الحضور (آخر 7 أيام)</h3>
                    <ResponsiveContainer width="100%" height={260}>
                      <LineChart data={analytics.charts.attendance_trend}>
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 100]} />
                        <Tooltip />
                        <Line type="monotone" dataKey="rate" stroke="#00A36C" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Card>
                </div>

                <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="employee-ranking">
                  <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">ترتيب الموظفين</h3>
                  <div className="space-y-2">
                    {analytics.employee_ranking.map((e) => (
                      <div key={e.employee_id} className="flex items-center justify-between p-3 border border-gray-100 rounded-sm" data-testid={`ranking-${e.employee_id}`}>
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
                    {analytics.employee_ranking.length === 0 && <p className="text-center text-gray-500 py-6">لا يوجد موظفون</p>}
                  </div>
                </Card>

                {analytics.insights.length > 0 && (
                  <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="insights-section">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">ملاحظات</h3>
                    <ul className="space-y-2">
                      {analytics.insights.map((insight, i) => (
                        <li key={i} className="text-sm text-gray-700 flex items-start gap-2" data-testid={`insight-${i}`}>
                          <span className="text-[#0033A0]">•</span> {insight}
                        </li>
                      ))}
                    </ul>
                  </Card>
                )}
              </div>
            )}
          </>
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
    </Layout>
  );
};

export default OwnerDashboard;
