import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Building2, Users, DollarSign, TrendingUp, AlertTriangle, Clock, Wifi, WifiOff } from 'lucide-react';

const SuperAdminDashboard = ({ onLogout, language, setLanguage }) => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStatistics();
    // Poll so company presence (online/offline) reflects real activity
    // without requiring a page refresh - no WebSocket infrastructure needed.
    const interval = setInterval(fetchStatistics, 10000);
    return () => clearInterval(interval);
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
    {
      title: 'الاشتراكات المنتهية',
      value: stats.expired_companies,
      icon: AlertTriangle,
      color: 'text-red-600',
      bg: 'bg-red-50',
    },
    {
      title: 'تنتهي خلال 7 أيام',
      value: stats.expiring_soon_count,
      icon: Clock,
      color: 'text-orange-600',
      bg: 'bg-orange-50',
    },
    {
      title: 'الشركات المتصلة',
      value: stats.companies_online,
      icon: Wifi,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
    {
      title: 'الشركات غير المتصلة',
      value: stats.companies_offline,
      icon: WifiOff,
      color: 'text-gray-500',
      bg: 'bg-gray-100',
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

        {!loading && stats && (
          <>
            <Card className="bg-white border border-gray-200 rounded-md shadow-sm" data-testid="expiring-soon-table">
              <div className="p-6 border-b border-gray-200">
                <h2 className="text-xl font-bold text-[#0A0A0A]">الشركات التي تنتهي قريباً</h2>
              </div>
              {stats.expiring_soon_companies.length === 0 ? (
                <div className="text-center py-8 text-gray-500">لا توجد اشتراكات تنتهي خلال 7 أيام</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50/50">
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">اسم الشركة</th>
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">تاريخ الانتهاء</th>
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الأيام المتبقية</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.expiring_soon_companies.map((c) => (
                        <tr key={c.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`expiring-soon-row-${c.id}`}>
                          <td className="px-6 py-4 font-medium text-[#0A0A0A]">{c.name}</td>
                          <td className="px-6 py-4 text-gray-600">{c.end_date}</td>
                          <td className="px-6 py-4 text-orange-600 font-medium">{c.remaining_days}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card className="bg-white border border-gray-200 rounded-md shadow-sm" data-testid="expired-companies-table">
              <div className="p-6 border-b border-gray-200">
                <h2 className="text-xl font-bold text-[#0A0A0A]">الشركات المنتهية</h2>
              </div>
              {stats.expired_companies_list.length === 0 ? (
                <div className="text-center py-8 text-gray-500">لا توجد اشتراكات منتهية</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-200 bg-gray-50/50">
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">اسم الشركة</th>
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">تاريخ الانتهاء</th>
                        <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.expired_companies_list.map((c) => (
                        <tr key={c.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`expired-row-${c.id}`}>
                          <td className="px-6 py-4 font-medium text-[#0A0A0A]">{c.name}</td>
                          <td className="px-6 py-4 text-gray-600">{c.end_date}</td>
                          <td className="px-6 py-4">
                            <span className="px-2.5 py-0.5 rounded-full text-xs font-medium border bg-red-50 text-red-700 border-red-200">منتهي</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </>
        )}
      </div>
    </Layout>
  );
};

export default SuperAdminDashboard;