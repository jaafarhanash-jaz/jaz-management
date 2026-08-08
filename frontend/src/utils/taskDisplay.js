// Shared task badge/label/format helpers - used by Owner/Tasks.js (active
// list + Task History) and CompletedTaskDetailDialog.js. Kept in one place
// so the two never drift, and so neither has to import from the other
// (which would be a circular import, since Owner/Tasks.js also imports
// CompletedTaskDetailDialog to render it).

export const PRIORITY_COLORS = {
  critical: 'bg-red-100 text-red-800 border-red-300',
  high: 'bg-red-50 text-red-700 border-red-200',
  medium: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  low: 'bg-blue-50 text-blue-700 border-blue-200',
};

export const STATUS_COLORS = {
  new: 'bg-blue-50 text-blue-700 border-blue-200',
  received: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  seen: 'bg-purple-50 text-purple-700 border-purple-200',
  in_progress: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  pending_review: 'bg-purple-50 text-purple-700 border-purple-200',
  completed: 'bg-green-50 text-green-700 border-green-200',
  rejected: 'bg-red-50 text-red-700 border-red-200',
  overdue: 'bg-red-50 text-red-700 border-red-200',
  cancelled: 'bg-gray-100 text-gray-500 border-gray-300',
  scheduled: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  pending_sequence: 'bg-gray-50 text-gray-500 border-gray-200',
};

export const STATUS_LABELS_AR = {
  new: 'جديدة (معلقة)', received: 'تم الاستلام', seen: 'شوهدت', in_progress: 'قيد التنفيذ', pending_review: 'بانتظار المراجعة',
  completed: 'مكتملة', rejected: 'مرفوضة', overdue: 'متأخرة', cancelled: 'ملغاة',
  scheduled: 'مجدولة', pending_sequence: 'بانتظار الدور',
};
export const PRIORITY_LABELS_AR = { high: 'عالية', medium: 'متوسطة', low: 'منخفضة', critical: 'عاجلة' };

export const getCategoryBadge = (task) => {
  if (task.task_category === 'urgent') return { label: '⚡ فورية', className: 'bg-red-50 text-red-700 border-red-200' };
  if (task.task_category === 'daily') return { label: '📋 يومية', className: 'bg-indigo-50 text-indigo-700 border-indigo-200' };
  return null;
};

export const formatTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'short', timeStyle: 'short' }) : null;

// Completion duration - derived purely client-side from the same
// created_at/completed_at fields the task already carries; no backend field.
export const formatDuration = (startIso, endIso) => {
  if (!startIso || !endIso) return '-';
  const ms = new Date(endIso) - new Date(startIso);
  if (!Number.isFinite(ms) || ms < 0) return '-';
  const totalMinutes = Math.floor(ms / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts = [];
  if (days) parts.push(`${days} يوم`);
  if (hours) parts.push(`${hours} ساعة`);
  if (minutes || parts.length === 0) parts.push(`${minutes} دقيقة`);
  return parts.join(' ');
};
