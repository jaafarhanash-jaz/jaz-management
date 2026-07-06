import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { FileText, Image as ImageIcon, Paperclip } from 'lucide-react';
import { toast } from 'sonner';

const OwnerReports = ({ onLogout, language, setLanguage }) => {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchReports(); }, []);

  const fetchReports = async () => {
    setLoading(true);
    try {
      const res = await api.get('/owner/reports');
      setReports(res.data);
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
  };

  const formatDate = (iso) => new Date(iso).toLocaleString('ar-EG');

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="reports-title">
            {t('reports', language)}
          </h1>
          <p className="text-gray-600 mt-2">تقارير الموظفين المرسلة</p>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : reports.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <FileText className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد تقارير حالياً</p>
          </Card>
        ) : (
          <div className="space-y-4">
            {reports.map((report) => (
              <Card key={report.id} className="p-6 bg-white border border-gray-200 rounded-md task-card" data-testid={`report-card-${report.id}`}>
                <div className="flex justify-between items-start gap-4 mb-3">
                  <div className="flex-1">
                    <h3 className="text-lg font-medium text-[#0A0A0A]">{report.title}</h3>
                    <p className="text-xs text-gray-500 mt-1">
                      بواسطة: <b>{report.employee_name}</b> • {formatDate(report.created_at)}
                    </p>
                  </div>
                  <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                    report.status === 'reviewed' ? 'bg-green-50 text-green-700 border-green-200' :
                    'bg-yellow-50 text-yellow-700 border-yellow-200'
                  }`}>
                    {report.status === 'reviewed' ? 'تمت المراجعة' : 'قيد الانتظار'}
                  </span>
                </div>
                <p className="text-sm text-gray-700 mb-4">{report.description}</p>
                
                {report.images && report.images.length > 0 && (
                  <div className="mb-3">
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">الصور</p>
                    <div className="flex flex-wrap gap-2">
                      {report.images.map((img, idx) => (
                        <img key={idx} src={img} alt={`img-${idx}`} className="w-20 h-20 object-cover rounded-sm border border-gray-200" />
                      ))}
                    </div>
                  </div>
                )}
                
                {report.files && report.files.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">الملفات</p>
                    <div className="flex flex-wrap gap-2">
                      {report.files.map((file, idx) => (
                        <div key={idx} className="flex items-center gap-1 text-xs bg-gray-50 px-3 py-1 rounded-sm border border-gray-200">
                          <Paperclip className="w-3 h-3" /> ملف {idx + 1}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>
    </Layout>
  );
};

export default OwnerReports;
