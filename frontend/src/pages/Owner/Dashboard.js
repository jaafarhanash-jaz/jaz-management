import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Users, CheckCircle, Clock, AlertCircle } from 'lucide-react';

const OwnerDashboard = ({ onLogout, language, setLanguage }) => {
  const [dashboard, setDashboard] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDashboard();
  }, []);

  const fetchDashboard = async () => {
    try {
      const response = await api.get('/owner/dashboard');
      setDashboard(response.data);
    } catch (error) {
      console.error('Error fetching dashboard:', error);
    } finally {
      setLoading(false);
    }
  };

  const statCards = dashboard ? [
    {
      title: t('totalEmployees', language),
      value: dashboard.total_employees,
      icon: Users,
      color: 'text-blue-600',
      bg: 'bg-blue-50',
    },
    {
      title: t('presentToday', language),
      value: dashboard.present_today,
      icon: CheckCircle,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      title: t('lateToday', language),
      value: dashboard.late_today,
      icon: Clock,
      color: 'text-yellow-600',
      bg: 'bg-yellow-50',
    },
    {
      title: t('absentToday', language),
      value: dashboard.absent_today,
      icon: AlertCircle,
      color: 'text-red-600',
      bg: 'bg-red-50',
    },
    {
      title: t('openTasks', language),
      value: dashboard.open_tasks,
      icon: CheckCircle,
      color: 'text-purple-600',
      bg: 'bg-purple-50',
    },
    {
      title: t('completedTasks', language),
      value: dashboard.completed_tasks,
      icon: CheckCircle,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      title: t('overdueTasks', language),
      value: dashboard.overdue_tasks,
      icon: AlertCircle,
      color: 'text-red-600',
      bg: 'bg-red-50',
    },
  ] : [];

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="owner-dashboard-title">
            {t('dashboard', language)}
          </h1>
          <p className="text-gray-600 mt-2">مرحباً بك في لوحة إدارة شركتك</p>
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
                    data-testid={`owner-stat-card-${index}`}
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

            {/* Recent Reports */}
            {dashboard.recent_reports && dashboard.recent_reports.length > 0 && (
              <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6">
                <h2 className="text-xl font-bold text-[#0A0A0A] mb-4">آخر التقارير</h2>
                <div className="space-y-3">
                  {dashboard.recent_reports.map((report) => (
                    <div
                      key={report.id}
                      className="p-4 border border-gray-100 rounded-sm hover:bg-gray-50 transition-colors"
                      data-testid={`report-${report.id}`}
                    >
                      <h3 className="font-medium text-[#0A0A0A]">{report.title}</h3>
                      <p className="text-sm text-gray-600 mt-1">{report.description}</p>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    </Layout>
  );
};

export default OwnerDashboard;