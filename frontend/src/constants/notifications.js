import {
  Bell,
  CalendarDays,
  CheckSquare,
  Clock,
  CreditCard,
  FileText,
  Mail,
  Megaphone,
  Siren,
  Wallet,
  Zap,
} from 'lucide-react';

// Icon/color/label metadata keyed by the notification's `category` field
// (see backend/services/notifications.py). Adding a brand-new category from
// a future module (Finance, CRM, Payroll, ...) only ever needs one new
// entry here on the frontend - the backend never needs to know about icons
// or colors, and categories with no entry fall back to the "system" look
// below rather than crashing.
export const CATEGORY_META = {
  system: { icon: Bell, color: 'text-slate-600', bg: 'bg-slate-50', label: 'النظام' },
  attendance: { icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50', label: 'الحضور والانصراف' },
  tasks: { icon: CheckSquare, color: 'text-purple-600', bg: 'bg-purple-50', label: 'المهام' },
  urgent_tasks: { icon: Zap, color: 'text-orange-600', bg: 'bg-orange-50', label: 'مهام فورية' },
  critical_tasks: { icon: Siren, color: 'text-red-600', bg: 'bg-red-50', label: 'مهام عاجلة' },
  messages: { icon: Mail, color: 'text-blue-600', bg: 'bg-blue-50', label: 'الرسائل' },
  calendar: { icon: CalendarDays, color: 'text-indigo-600', bg: 'bg-indigo-50', label: 'التقويم' },
  reports: { icon: FileText, color: 'text-teal-600', bg: 'bg-teal-50', label: 'التقارير' },
  announcements: { icon: Megaphone, color: 'text-pink-600', bg: 'bg-pink-50', label: 'إعلانات' },
  subscriptions: { icon: CreditCard, color: 'text-emerald-600', bg: 'bg-emerald-50', label: 'الاشتراك' },
  payments: { icon: Wallet, color: 'text-green-600', bg: 'bg-green-50', label: 'المدفوعات' },
};

export function categoryMeta(category) {
  return CATEGORY_META[category] || CATEGORY_META.system;
}

// Used only when a notification has no stored action_url (e.g. a
// notification created before this field existed) - routes to the role-
// appropriate landing page for that category instead of doing nothing.
export function fallbackActionUrl(category, role) {
  const isOwner = role === 'company_owner';
  switch (category) {
    case 'tasks':
    case 'urgent_tasks':
    case 'critical_tasks':
      return isOwner ? '/company-owner/tasks' : '/employee/tasks';
    case 'attendance':
      return isOwner ? '/company-owner/attendance' : '/employee/attendance';
    case 'messages':
      return isOwner ? '/company-owner/messages' : '/employee/messages';
    case 'calendar':
      return isOwner ? '/company-owner/calendar' : '/employee/calendar';
    case 'reports':
      return isOwner ? '/company-owner/reports' : '/employee/reports';
    case 'subscriptions':
      return '/company-owner/subscription';
    case 'announcements':
      return isOwner ? '/company-owner/announcements' : '/employee/announcements';
    default:
      return isOwner ? '/company-owner/dashboard' : '/employee/dashboard';
  }
}
