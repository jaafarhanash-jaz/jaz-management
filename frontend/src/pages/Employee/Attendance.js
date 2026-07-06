import { useEffect, useState, useRef } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Html5Qrcode } from 'html5-qrcode';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { QrCode, LogIn, LogOut, MapPin, Clock } from 'lucide-react';
import { toast } from 'sonner';

const EmployeeAttendance = ({ onLogout, language, setLanguage }) => {
  const [history, setHistory] = useState([]);
  const [today, setToday] = useState(null);
  const [loading, setLoading] = useState(true);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [scanMode, setScanMode] = useState('checkin'); // checkin or checkout
  const scannerRef = useRef(null);

  useEffect(() => { fetchAll(); }, []);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const res = await api.get('/employee/attendance/history');
      setHistory(res.data);
      const todayStr = new Date().toISOString().split('T')[0];
      setToday(res.data.find(r => r.date === todayStr));
    } catch (e) { toast.error('خطأ في جلب البيانات'); }
    setLoading(false);
  };

  const getLocation = () => new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude }),
      () => resolve(null),
      { timeout: 5000 }
    );
  });

  const doCheckIn = async (qrCode) => {
    const loc = await getLocation();
    try {
      await api.post('/employee/attendance/check-in', { qr_code: qrCode, ...loc });
      toast.success('تم تسجيل الحضور بنجاح');
      fetchAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const doCheckOut = async () => {
    const loc = await getLocation();
    try {
      await api.post('/employee/attendance/check-out', loc || {});
      toast.success('تم تسجيل الانصراف بنجاح');
      fetchAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const startScanner = async (mode) => {
    setScanMode(mode);
    setScannerOpen(true);
    setTimeout(async () => {
      try {
        const html5QrCode = new Html5Qrcode('qr-reader');
        scannerRef.current = html5QrCode;
        await html5QrCode.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: 250, height: 250 } },
          async (decodedText) => {
            await stopScanner();
            if (mode === 'checkin') await doCheckIn(decodedText);
            else await doCheckOut();
          }
        );
      } catch (err) {
        toast.error('خطأ في تشغيل الكاميرا');
      }
    }, 500);
  };

  const stopScanner = async () => {
    if (scannerRef.current) {
      try { await scannerRef.current.stop(); await scannerRef.current.clear(); } catch (e) {}
      scannerRef.current = null;
    }
    setScannerOpen(false);
  };

  const formatTime = (iso) => iso ? new Date(iso).toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' }) : '-';

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-attendance-title">
            {t('attendance', language)}
          </h1>
          <p className="text-gray-600 mt-2">حضوري وانصرافي</p>
        </div>

        {/* Today's Status */}
        <Card className="p-6 bg-white border border-gray-200 rounded-md shadow-sm">
          <h2 className="text-xl font-bold text-[#0A0A0A] mb-4">اليوم</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div className="p-4 border border-gray-200 rounded-sm">
              <div className="flex items-center gap-2 text-gray-500 mb-1">
                <LogIn className="w-4 h-4" /><span className="text-xs uppercase tracking-wider">وقت الدخول</span>
              </div>
              <p className="text-2xl font-bold text-[#0A0A0A]">{today?.check_in_time ? formatTime(today.check_in_time) : '-'}</p>
            </div>
            <div className="p-4 border border-gray-200 rounded-sm">
              <div className="flex items-center gap-2 text-gray-500 mb-1">
                <LogOut className="w-4 h-4" /><span className="text-xs uppercase tracking-wider">وقت الخروج</span>
              </div>
              <p className="text-2xl font-bold text-[#0A0A0A]">{today?.check_out_time ? formatTime(today.check_out_time) : '-'}</p>
            </div>
          </div>
          <div className="flex gap-3 flex-wrap">
            {!today?.check_in_time && (
              <Button onClick={() => startScanner('checkin')} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="scan-checkin-btn">
                <QrCode className="w-4 h-4 me-2" /> Scan QR - تسجيل حضور
              </Button>
            )}
            {today?.check_in_time && !today?.check_out_time && (
              <Button onClick={doCheckOut} className="bg-red-600 hover:bg-red-700 text-white" data-testid="checkout-btn">
                <LogOut className="w-4 h-4 me-2" /> تسجيل انصراف
              </Button>
            )}
          </div>
        </Card>

        {/* History */}
        <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
          <div className="p-6 border-b border-gray-200">
            <h2 className="text-xl font-bold text-[#0A0A0A]">سجل الحضور</h2>
          </div>
          {loading ? (
            <div className="text-center py-12 text-gray-500">Loading...</div>
          ) : history.length === 0 ? (
            <div className="text-center py-12">
              <Clock className="w-12 h-12 mx-auto text-gray-300 mb-4" />
              <p className="text-gray-500">لا توجد سجلات حضور</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">التاريخ</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الدخول</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الخروج</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((rec) => (
                    <tr key={rec.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`history-row-${rec.id}`}>
                      <td className="px-6 py-4 font-medium">{rec.date}</td>
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Dialog open={scannerOpen} onOpenChange={(open) => { if (!open) stopScanner(); }}>
          <DialogContent className="max-w-md" data-testid="scanner-dialog">
            <DialogHeader><DialogTitle>مسح رمز QR</DialogTitle></DialogHeader>
            <div id="qr-reader" className="w-full"></div>
            <p className="text-xs text-gray-500 text-center">وجّه الكاميرا نحو رمز QR الخاص بشركتك</p>
            <Button variant="outline" onClick={stopScanner}>إلغاء</Button>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default EmployeeAttendance;
