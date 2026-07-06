import { Layout } from '@/components/Layout';
import { t } from '@/utils/translations';

const OwnerDepartments = ({ onLogout, language, setLanguage }) => {
  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="departments-title">
          {t('departments', language)}
        </h1>
        <p className="text-gray-600">إدارة الأقسام</p>
      </div>
    </Layout>
  );
};

export default OwnerDepartments;