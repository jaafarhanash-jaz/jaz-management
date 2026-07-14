import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { CheckCircle, Clock, TrendingUp, Bell, CalendarDays, Timer, Gift, MailQuestion, PartyPopper } from 'lucide-react';

const EmployeeDashboard = ({ onLogout, language, setLanguage }) => {
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDashboard();
  }, []);

  const fetchDashboard = async () => {
    setLoading(true);
    try {
      const response = await api.get('/employee/dashboard');
      setDashboard(response.data);
    } catch (error) {
      // A failed fetch must not leave the page "not loading" with no data -
      // the render below only enters its data-dependent branch once
      // `dashboard` is actually populated, so this can safely leave it null
      // without risking a crash. Unlike Owner Dashboard this page has no
      // auto-poll, so an explicit retry action is the only recovery path.
      console.error('Error fetching dashboard:', error);
    } finally {
      setLoading(false);
    }
  };

  const statCards = dashboard ? [
    {
      title: 'إجمالي المهام',
      value: dashboard.total_tasks,
      icon: CheckCircle,
      color: 'text-blue-600',
      bg: 'bg-blue-50',
    },
    {
      title: 'مهام مكتملة',
      value: dashboard.completed_tasks,
      icon: CheckCircle,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      title: 'مهام معلقة',
      value: dashboard.pending_tasks,
      icon: Clock,
      color: 'text-yellow-600',
      bg: 'bg-yellow-50',
    },
    {
      title: 'نسبة الإنجاز',
      value: `${dashboard.completion_rate}%`,
      icon: TrendingUp,
      color: 'text-purple-600',
      bg: 'bg-purple-50',
    },
  ] : [];

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-dashboard-title">
            {t('dashboard', language)}
          </h1>
          <p className="text-gray-600 mt-2">مرحباً بك في لوحة التحكم</p>
        </div>

        {/* Company Holiday banner - additive, only rendered when today is an
            active company holiday or a configured weekly holiday. */}
        {dashboard?.today_is_holiday && (
          <Card className="p-4 bg-emerald-50 border border-emerald-200 rounded-md flex items-center gap-3" data-testid="today-holiday-banner">
            <PartyPopper className="w-6 h-6 text-emerald-600 flex-shrink-0" />
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
          // The fetch failed - render an explicit, recoverable state instead
          // of falling through into content that assumes `dashboard` exists.
          <div className="text-center py-12 text-gray-500" data-testid="employee-dashboard-error">
            <p className="mb-4">تعذر تحميل بيانات لوحة التحكم. تحقق من الاتصال وحاول مرة أخرى.</p>
            <Button onClick={fetchDashboard} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="employee-dashboard-retry-btn">
              إعادة المحاولة
            </Button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {statCards.map((card, index) => {
                const Icon = card.icon;
                return (
                  <Card
                    key={index}
                    data-testid={`employee-stat-card-${index}`}
                    className="stat-card bg-white border border-gray-200 rounded-md shadow-sm p-6"
                  >
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

            {/* Attendance Status */}
            <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6">
              <h2 className="text-xl font-bold text-[#0A0A0A] mb-4">الحضور اليوم</h2>
              <div className="flex gap-4">
                {dashboard.checked_in ? (
                  <div className="flex items-center gap-2 text-green-600">
                    <CheckCircle className="w-5 h-5" />
                    <span>تم تسجيل الحضور</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-gray-600">
                    <Clock className="w-5 h-5" />
                    <span>لم يتم تسجيل الحضور</span>
                  </div>
                )}
                {dashboard.checked_out && (
                  <div className="flex items-center gap-2 text-blue-600">
                    <CheckCircle className="w-5 h-5" />
                    <span>تم تسجيل الانصراف</span>
                  </div>
                )}
              </div>
            </Card>

            {/* Calendar Widgets - additive section, existing dashboard content above is unchanged */}
            {dashboard.calendar_widgets && (
              <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6" data-testid="employee-calendar-widgets">
                <div className="flex items-center gap-2 mb-4">
                  <CalendarDays className="w-5 h-5 text-[#0033A0]" />
                  <h2 className="text-xl font-bold text-[#0A0A0A]">جدول اليوم</h2>
                </div>
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
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
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
              </Card>
            )}

            {/* Latest Notification */}
            {dashboard.latest_notification && (
              <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6">
                <h2 className="text-xl font-bold text-[#0A0A0A] mb-4">آخر إشعار</h2>
                <div className="flex items-start gap-3">
                  <Bell className="w-5 h-5 text-blue-600 mt-1" />
                  <div>
                    <h3 className="font-medium text-[#0A0A0A]">{dashboard.latest_notification.title}</h3>
                    <p className="text-sm text-gray-600 mt-1">{dashboard.latest_notification.message}</p>
                  </div>
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    </Layout>
  );
};

export default EmployeeDashboard;