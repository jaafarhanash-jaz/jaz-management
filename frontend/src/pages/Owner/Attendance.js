import { Layout } from '@/components/Layout';
import { t } from '@/utils/translations';

const OwnerAttendance = ({ onLogout, language, setLanguage }) => {
  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="attendance-title">
          {t('attendance', language)}
        </h1>
        <p className="text-gray-600">متابعة الحضور والانصراف</p>
      </div>
    </Layout>
  );
};

export default OwnerAttendance;