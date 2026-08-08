import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Download, Paperclip, UserCircle, UserCheck, CalendarPlus, CalendarCheck2, Clock, FileText } from 'lucide-react';
import ImageLightbox from '@/components/ImageLightbox';
import {
  PRIORITY_COLORS, STATUS_COLORS, PRIORITY_LABELS_AR, STATUS_LABELS_AR,
  getCategoryBadge, formatTime, formatDuration,
} from '@/utils/taskDisplay';

const downloadDataUrl = (dataUrl, filename) => {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  link.click();
};

const Field = ({ label, value, icon: Icon }) => (
  <div>
    <p className="text-xs text-gray-500 flex items-center gap-1">{Icon && <Icon className="w-3 h-3" />} {label}</p>
    <p className="text-sm text-[#0A0A0A] font-medium mt-0.5">{value || '-'}</p>
  </div>
);

// Read-only review screen for a single completed task - opened from Task
// History (Owner/Tasks.js), fed the task object it already has in memory
// (list_completed_tasks_for_owner already returns proof_files/attachments
// inline, so this dialog makes zero API calls of its own).
const CompletedTaskDetailDialog = ({ task, open, onOpenChange }) => {
  const [lightboxIndex, setLightboxIndex] = useState(null);

  if (!task) return null;

  const categoryBadge = getCategoryBadge(task);
  const proofFiles = task.proof_files || [];
  const attachments = task.attachments || [];
  const proofImages = proofFiles.filter((f) => f.startsWith('data:image'));
  const proofOther = proofFiles.filter((f) => !f.startsWith('data:image'));
  const imageAttachments = attachments.filter((a) => a.attachment_type === 'image');
  const otherAttachments = attachments.filter((a) => a.attachment_type !== 'image');

  // One combined gallery so the viewer can browse proof images and
  // image-type attachments together, in the order they're displayed.
  const gallery = [
    ...proofImages.map((src, i) => ({ src, label: `صورة إثبات ${i + 1}` })),
    ...imageAttachments.map((a) => ({ src: a.data, label: a.filename })),
  ];

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="completed-task-detail-dialog">
          <DialogHeader>
            <div className="flex items-center gap-2 flex-wrap pe-6">
              {categoryBadge && (
                <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${categoryBadge.className}`}>{categoryBadge.label}</span>
              )}
              <DialogTitle className="text-xl">{task.title}</DialogTitle>
              <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[task.priority]}`}>
                {PRIORITY_LABELS_AR[task.priority]}
              </span>
              <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[task.status]}`}>
                {STATUS_LABELS_AR[task.status] || task.status}
              </span>
            </div>
          </DialogHeader>

          <div className="space-y-6">
            <section>
              <h3 className="text-sm font-bold text-[#0A0A0A] mb-3">معلومات المهمة</h3>
              <p className="text-sm text-gray-700 bg-gray-50 border border-gray-100 rounded-md p-3 mb-3 whitespace-pre-wrap">{task.description}</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <Field label="الموظف المكلّف" value={task.assigned_to_name} icon={UserCircle} />
                <Field label="بواسطة" value={task.created_by_name} icon={UserCircle} />
                <Field label="تاريخ الإنشاء" value={formatTime(task.created_at)} icon={CalendarPlus} />
                <Field label="موعد التسليم" value={task.due_date ? `${task.due_date}${task.due_time ? ` ${task.due_time}` : ''}` : null} />
                <Field label="تاريخ الإكمال" value={formatTime(task.completed_at)} icon={CalendarCheck2} />
                <Field label="مدة الإنجاز" value={formatDuration(task.created_at, task.completed_at)} icon={Clock} />
              </div>
            </section>

            <section className="border-t border-gray-100 pt-4">
              <h3 className="text-sm font-bold text-[#0A0A0A] mb-3">تفاصيل الإكمال</h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <Field label="أكملها الموظف" value={task.completed_by_name} icon={UserCheck} />
                <Field label="وقت الإكمال بالتحديد" value={formatTime(task.completed_at)} icon={Clock} />
              </div>
            </section>

            {gallery.length > 0 && (
              <section className="border-t border-gray-100 pt-4">
                <h3 className="text-sm font-bold text-[#0A0A0A] mb-3">صور الإثبات</h3>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-3" data-testid="proof-image-grid">
                  {gallery.map((img, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setLightboxIndex(i)}
                      className="aspect-square rounded-md overflow-hidden border border-gray-200 hover:ring-2 hover:ring-[#0033A0] transition-all"
                      data-testid={`proof-thumbnail-${i}`}
                    >
                      <img src={img.src} alt={img.label} loading="lazy" decoding="async" className="w-full h-full object-cover" />
                    </button>
                  ))}
                </div>
              </section>
            )}

            {(proofOther.length > 0 || otherAttachments.length > 0) && (
              <section className="border-t border-gray-100 pt-4">
                <h3 className="text-sm font-bold text-[#0A0A0A] mb-3">المرفقات</h3>
                <div className="flex flex-wrap gap-2" data-testid="attachments-list">
                  {proofOther.map((f, i) => (
                    <button
                      key={`proof-${i}`}
                      type="button"
                      onClick={() => downloadDataUrl(f, `إثبات-${i + 1}`)}
                      className="flex items-center gap-1 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1 hover:bg-gray-100"
                    >
                      <FileText className="w-3 h-3" /> ملف إثبات {i + 1}
                    </button>
                  ))}
                  {otherAttachments.map((a, i) => (
                    <button
                      key={`att-${i}`}
                      type="button"
                      onClick={() => downloadDataUrl(a.data, a.filename)}
                      className="flex items-center gap-1 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1 hover:bg-gray-100"
                      data-testid={`attachment-download-${i}`}
                    >
                      <Paperclip className="w-3 h-3" /> {a.filename}
                      <Download className="w-3 h-3 text-gray-400" />
                    </button>
                  ))}
                </div>
              </section>
            )}

            {gallery.length === 0 && proofOther.length === 0 && otherAttachments.length === 0 && (
              <p className="text-xs text-gray-400 border-t border-gray-100 pt-4">لا توجد صور إثبات أو مرفقات لهذه المهمة.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {lightboxIndex !== null && (
        <ImageLightbox
          images={gallery}
          index={lightboxIndex}
          onNavigate={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
        />
      )}
    </>
  );
};

export default CompletedTaskDetailDialog;
