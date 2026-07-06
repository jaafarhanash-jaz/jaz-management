import { Layout } from '@/components/Layout';
import { t } from '@/utils/translations';

const EmployeePerformance = ({ onLogout, language, setLanguage }) => {
  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-performance-title">
          {t('performance', language)}
        </h1>
        <p className="text-gray-600">أدائي</p>
      </div>
    </Layout>
  );
};

export default EmployeePerformance;