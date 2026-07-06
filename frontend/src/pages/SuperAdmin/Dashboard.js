import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Building2, Users, DollarSign, TrendingUp } from 'lucide-react';

const SuperAdminDashboard = ({ onLogout, language, setLanguage }) => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStatistics();
  }, []);

  const fetchStatistics = async () => {
    try {
      const response = await api.get('/admin/statistics');
      setStats(response.data);
    } catch (error) {
      console.error('Error fetching statistics:', error);
    } finally {
      setLoading(false);
    }
  };

  const statCards = stats ? [
    {
      title: t('totalCompanies', language),
      value: stats.total_companies,
      icon: Building2,
      color: 'text-blue-600',
      bg: 'bg-blue-50',
    },
    {
      title: t('activeCompanies', language),
      value: stats.active_companies,
      icon: TrendingUp,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      title: t('totalEmployees', language),
      value: stats.total_employees,
      icon: Users,
      color: 'text-purple-600',
      bg: 'bg-purple-50',
    },
    {
      title: t('totalRevenue', language),
      value: `$${stats.total_revenue}`,
      icon: DollarSign,
      color: 'text-yellow-600',
      bg: 'bg-yellow-50',
    },
  ] : [];

  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="dashboard-title">
            {t('dashboard', language)}
          </h1>
          <p className="text-gray-600 mt-2">مرحباً بك في لوحة التحكم الرئيسية</p>
        </div>

        {loading ? (
          <div className="text-center py-12">Loading...</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {statCards.map((card, index) => {
              const Icon = card.icon;
              return (
                <Card
                  key={index}
                  data-testid={`stat-card-${index}`}
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
        )}
      </div>
    </Layout>
  );
};

export default SuperAdminDashboard;