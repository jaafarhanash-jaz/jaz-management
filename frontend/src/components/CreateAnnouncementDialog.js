import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import api from '@/utils/api';
import { toast } from 'sonner';
import { Megaphone } from 'lucide-react';

const emptyForm = { title: '', message: '' };

// POST /owner/announcements - creates one Announcement row and broadcasts a
// real notification (category=announcements) to every employee via the
// existing notification framework. No mock recipients, no fake delivery
// count - the response is the real created row.
const CreateAnnouncementDialog = ({ open, onOpenChange, onCreated }) => {
  const [form, setForm] = useState(emptyForm);
  const [submitting, setSubmitting] = useState(false);

  const close = () => {
    onOpenChange(false);
    setForm(emptyForm);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post('/owner/announcements', form);
      toast.success('تم نشر الإعلان وإرساله لجميع الموظفين');
      onCreated?.();
      close();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'حدث خطأ');
    }
    setSubmitting(false);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); }}>
      <DialogContent className="max-w-md" data-testid="create-announcement-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Megaphone className="w-5 h-5 text-pink-600" /> إعلان جديد
          </DialogTitle>
          <DialogDescription>يصل هذا الإعلان فوراً لجميع موظفي الشركة عبر مركز الإشعارات.</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <Label>العنوان</Label>
            <Input data-testid="announcement-title-input" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>
          <div>
            <Label>نص الإعلان</Label>
            <Textarea data-testid="announcement-message-input" required rows={4} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close} data-testid="cancel-announcement-btn">إلغاء</Button>
            <Button type="submit" disabled={submitting} className="bg-pink-600 hover:bg-pink-700 text-white" data-testid="publish-announcement-btn">
              {submitting ? 'جارِ النشر...' : 'نشر الإعلان'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default CreateAnnouncementDialog;
