import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import CreateAnnouncementDialog from '@/components/CreateAnnouncementDialog';
import api from '@/utils/api';
import { Megaphone, Plus } from 'lucide-react';
import { toast } from 'sonner';

const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' }) : '-';

// Shared between the owner (create + view) and employee (view only) roles -
// same real GET endpoint per role (/owner/announcements or
// /employee/announcements), same Announcement shape, no separate
// implementation duplicated per role.
const Announcements = ({ onLogout, language, setLanguage, userRole }) => {
  const [announcements, setAnnouncements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const isOwner = userRole === 'company_owner';

  const fetchAnnouncements = async () => {
    setLoading(true);
    try {
      const res = await api.get(isOwner ? '/owner/announcements' : '/employee/announcements');
      setAnnouncements(res.data);
    } catch (e) {
      toast.error('خطأ في جلب الإعلانات');
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchAnnouncements();
    const interval = setInterval(fetchAnnouncements, 15000);
    return () => clearInterval(interval);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center flex-wrap gap-3">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="announcements-title">الإعلانات</h1>
            <p className="text-gray-600 mt-2">{isOwner ? 'إعلانات نشرتها لجميع الموظفين' : 'إعلانات الشركة'}</p>
          </div>
          {isOwner && (
            <Button onClick={() => setDialogOpen(true)} className="bg-pink-600 hover:bg-pink-700 text-white rounded-sm" data-testid="new-announcement-btn">
              <Plus className="w-4 h-4 me-2" /> إعلان جديد
            </Button>
          )}
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : announcements.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200 animate-in fade-in duration-300">
            <Megaphone className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">لا توجد إعلانات حتى الآن</p>
          </Card>
        ) : (
          <div className="space-y-3">
            {announcements.map((a) => (
              <Card key={a.id} className="p-6 bg-white border border-gray-200 rounded-md transition-shadow hover:shadow-md" data-testid={`announcement-${a.id}`}>
                <div className="flex items-start gap-3">
                  <div className="p-2.5 rounded-sm bg-pink-50 flex-shrink-0">
                    <Megaphone className="w-5 h-5 text-pink-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-bold text-[#0A0A0A]">{a.title}</h3>
                    <p className="text-sm text-gray-700 mt-1 whitespace-pre-wrap">{a.message}</p>
                    <p className="text-xs text-gray-400 mt-2">{a.created_by_name} · {formatDateTime(a.created_at)}</p>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      {isOwner && (
        <CreateAnnouncementDialog open={dialogOpen} onOpenChange={setDialogOpen} onCreated={fetchAnnouncements} />
      )}
    </Layout>
  );
};

export default Announcements;
