import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Plus } from 'lucide-react';

const SuperAdminCompanies = ({ onLogout, language, setLanguage }) => {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchCompanies();
  }, []);

  const fetchCompanies = async () => {
    try {
      const response = await api.get('/admin/companies');
      setCompanies(response.data);
    } catch (error) {
      console.error('Error fetching companies:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="companies-title">
            {t('companies', language)}
          </h1>
          <Button
            data-testid="add-company-btn"
            className="bg-[#0033A0] text-white hover:bg-[#002277] rounded-sm"
          >
            <Plus className="w-4 h-4 me-2" />
            {t('add', language)}
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12">Loading...</div>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-start">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="text-start px-6 py-4 text-xs font-medium text-gray-500 uppercase tracking-wider">
                      الشركة
                    </th>
                    <th className="text-start px-6 py-4 text-xs font-medium text-gray-500 uppercase tracking-wider">
                      عدد الموظفين
                    </th>
                    <th className="text-start px-6 py-4 text-xs font-medium text-gray-500 uppercase tracking-wider">
                      الحالة
                    </th>
                    <th className="text-start px-6 py-4 text-xs font-medium text-gray-500 uppercase tracking-wider">
                      الإجراءات
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((company) => (
                    <tr
                      key={company.id}
                      className="border-b border-gray-100 hover:bg-gray-50/50 transition-colors"
                      data-testid={`company-row-${company.id}`}
                    >
                      <td className="px-6 py-4">
                        <div>
                          <p className="font-medium text-[#0A0A0A]">{company.name}</p>
                          <p className="text-xs text-gray-500">{company.address}</p>
                        </div>
                      </td>
                      <td className="px-6 py-4">{company.employee_count}</td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                            company.subscription_status === 'active'
                              ? 'bg-green-50 text-green-700 border-green-200'
                              : 'bg-gray-50 text-gray-700 border-gray-200'
                          }`}
                        >
                          {company.subscription_status === 'active' ? 'نشط' : 'غير نشط'}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex gap-2">
                          <Button variant="ghost" size="sm" data-testid={`edit-company-${company.id}`}>
                            {t('edit', language)}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-600 hover:text-red-700 hover:bg-red-50"
                            data-testid={`delete-company-${company.id}`}
                          >
                            {t('delete', language)}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </Layout>
  );
};

export default SuperAdminCompanies;