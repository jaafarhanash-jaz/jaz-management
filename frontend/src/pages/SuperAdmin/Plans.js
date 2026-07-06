import { Layout } from '@/components/Layout';
import { t } from '@/utils/translations';

const SuperAdminPlans = ({ onLogout, language, setLanguage }) => {
  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="plans-title">
          {t('plans', language)}
        </h1>
        <p className="text-gray-600">سيتم إضافة إدارة خطط الاشتراك قريباً</p>
      </div>
    </Layout>
  );
};

export default SuperAdminPlans;