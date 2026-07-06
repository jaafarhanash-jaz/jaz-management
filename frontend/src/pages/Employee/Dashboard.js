import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { CheckCircle, Clock, TrendingUp, Bell } from 'lucide-react';

const EmployeeDashboard = ({ onLogout, language, setLanguage }) => {
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDashboard();
  }, []);

  const fetchDashboard = async () => {
    try {
      const response = await api.get('/employee/dashboard');
      setDashboard(response.data);
    } catch (error) {
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

        {loading ? (
          <div className="text-center py-12">Loading...</div>
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