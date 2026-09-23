import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import CreateCompanyNotificationDialog from '@/components/CreateCompanyNotificationDialog';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Bell, Plus } from 'lucide-react';
import { toast } from 'sonner';

const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' }) : '-';

const RECIPIENT_MODE_LABELS = {
  all_managers: 'جميع المدراء',
  all_employees: 'جميع الموظفين',
  everyone: 'الجميع',
  custom: 'تحديد المستلمين',
};

const getStatusBadge = (status) => {
  if (status === 'sent') return { label: 'تم الإرسال', className: 'bg-green-50 text-green-700 border-green-200' };
  if (status === 'partial_failure') return { label: 'إرسال جزئي', className: 'bg-orange-50 text-orange-700 border-orange-200' };
  if (status === 'failed') return { label: 'فشل الإرسال', className: 'bg-red-50 text-red-700 border-red-200' };
  return { label: status || '-', className: 'bg-gray-50 text-gray-700 border-gray-200' };
};

// Super Admin > Company Notifications - creates a platform-wide broadcast
// (managers/employees/everyone/custom company selection) and shows the
// send history. Sending fans out through the existing per-user
// Notification/publish() pipeline (real in-app + push delivery), same
// mechanism the Announcements feature already uses for its own narrower
// company_owner-to-employees case.
const SuperAdminCompanyNotifications = ({ onLogout, language, setLanguage }) => {
  const [broadcasts, setBroadcasts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);

  const fetchBroadcasts = async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/company-notifications');
      setBroadcasts(res.data);
    } catch (e) {
      toast.error('خطأ في جلب سجل الإشعارات');
    }
    setLoading(false);
  };

  useEffect(() => { fetchBroadcasts(); }, []);

  return (
    <Layout userRole="super_admin" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center flex-wrap gap-3">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="company-notifications-title">
              {t('companyNotifications', language)}
            </h1>
            <p className="text-gray-600 mt-2">إرسال إشعارات موحدة للشركات والمستخدمين المسجلين في منصة JAZ</p>
          </div>
          <Button onClick={() => setDialogOpen(true)} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="new-company-notification-btn">
            <Plus className="w-4 h-4 me-2" /> إشعار جديد
          </Button>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : broadcasts.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200">
            <Bell className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لم يتم إرسال أي إشعارات بعد</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50/50">
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">العنوان</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">المستلمون</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">العدد</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase" title="عدد المستلمين الذين أُنشئ لهم إشعار داخل التطبيق">تم الإنشاء</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase" title="عدد المستلمين الذين تم تأكيد وصول إشعار الدفع (Push) لأحد أجهزتهم">وصل الدفع</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase" title="عدد المستلمين الذين لديهم جهاز مسجل لكن فشل إرسال الدفع لجميع أجهزتهم">فشل الدفع</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">المرسل</th>
                    <th className="text-start px-6 py-3 text-xs font-medium text-gray-500 uppercase">التاريخ</th>
                  </tr>
                </thead>
                <tbody>
                  {broadcasts.map((b) => {
                    const badge = getStatusBadge(b.status);
                    return (
                      <tr key={b.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`broadcast-row-${b.id}`}>
                        <td className="px-6 py-4">
                          <p className="font-medium text-[#0A0A0A]">{b.title_ar}</p>
                          <p className="text-xs text-gray-500" dir="ltr">{b.title_en}</p>
                        </td>
                        <td className="px-6 py-4 text-gray-600">{RECIPIENT_MODE_LABELS[b.recipient_mode] || b.recipient_mode}</td>
                        <td className="px-6 py-4">{b.recipient_count}</td>
                        <td className="px-6 py-4 text-gray-700">{b.notification_created_count}</td>
                        <td className="px-6 py-4 text-green-700">{b.push_delivered_count}</td>
                        <td className="px-6 py-4 text-red-600">{b.push_failed_count}</td>
                        <td className="px-6 py-4">
                          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border ${badge.className}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-gray-600">{b.sender_name}</td>
                        <td className="px-6 py-4 text-gray-500 text-xs">{formatDateTime(b.sent_at || b.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      <CreateCompanyNotificationDialog open={dialogOpen} onOpenChange={setDialogOpen} onCreated={fetchBroadcasts} />
    </Layout>
  );
};

export default SuperAdminCompanyNotifications;
