import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Clock, CheckCircle, MapPin } from 'lucide-react';
import { toast } from 'sonner';

const OwnerAttendance = ({ onLogout, language, setLanguage }) => {
  const [attendance, setAttendance] = useState([]);
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState(new Date().toISOString().split('T')[0]);

  useEffect(() => { fetchAttendance(); }, [date]);

  const fetchAttendance = async () => {
    setLoading(true);
    try {
      const res = await api.get(`/owner/attendance?date=${date}`);
      setAttendance(res.data);
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
  };

  const formatTime = (iso) => {
    if (!iso) return '-';
    return new Date(iso).toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' });
  };

  const openMap = (loc) => {
    if (loc && loc.latitude && loc.longitude) {
      window.open(`https://www.openstreetmap.org/?mlat=${loc.latitude}&mlon=${loc.longitude}#map=17/${loc.latitude}/${loc.longitude}`, '_blank');
    }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-end flex-wrap gap-4">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="attendance-title">
              {t('attendance', language)}
            </h1>
            <p className="text-gray-600 mt-2">متابعة الحضور والانصراف</p>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">التاريخ</label>
            <Input data-testid="attendance-date-input" type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-48" />
          </div>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : attendance.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <Clock className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد سجلات حضور لهذا التاريخ</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الموظف</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">وقت الدخول</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">وقت الخروج</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الموقع</th>
                  </tr>
                </thead>
                <tbody>
                  {attendance.map((rec) => (
                    <tr key={rec.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`attendance-row-${rec.id}`}>
                      <td className="px-6 py-4 font-medium text-[#0A0A0A]">{rec.employee_name}</td>
                      <td className="px-6 py-4 text-gray-600">{formatTime(rec.check_in_time)}</td>
                      <td className="px-6 py-4 text-gray-600">{formatTime(rec.check_out_time)}</td>
                      <td className="px-6 py-4">
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                          rec.status === 'present' ? 'bg-green-50 text-green-700 border-green-200' :
                          rec.status === 'late' ? 'bg-yellow-50 text-yellow-700 border-yellow-200' :
                          'bg-red-50 text-red-700 border-red-200'
                        }`}>
                          {rec.status === 'present' ? 'حاضر' : rec.status === 'late' ? 'متأخر' : 'غائب'}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        {rec.check_in_location ? (
                          <button onClick={() => openMap(rec.check_in_location)} className="text-[#0033A0] hover:underline flex items-center gap-1 text-xs" data-testid={`map-btn-${rec.id}`}>
                            <MapPin className="w-3 h-3" /> عرض الموقع
                          </button>
                        ) : <span className="text-gray-400 text-xs">-</span>}
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

export default OwnerAttendance;
