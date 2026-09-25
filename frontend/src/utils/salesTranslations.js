// JAZ Sales strings. Kept in their own file (not utils/translations.js) so the
// Sales module stays self-contained and reviewable on its own. Arabic is the
// primary language, matching the rest of the platform. Phase 1 (workspace, team)
// lives below; Phase 2 (leads, campaigns, pipeline) is in salesLeadsTranslations.js
// Phase 3 (calls, follow-ups, demos, trials) in salesActivitiesTranslations.js, Phase 4 (conversion, customers,
// onboarding) in salesCustomersTranslations.js and Phase 5 (dashboard, reports) in salesReportsTranslations.js; all are
// merged in at the bottom of this file.
import { leadsTranslations } from '@/utils/salesLeadsTranslations';
import { activitiesTranslations } from '@/utils/salesActivitiesTranslations';
import { customersTranslations } from '@/utils/salesCustomersTranslations';
import { reportsTranslations } from '@/utils/salesReportsTranslations';

const baseTranslations = {
  ar: {
    brand: 'مبيعات JAZ',
    nav_home: 'لوحة المبيعات',
    nav_team: 'الفريق',
    nav_profile: 'الملف الشخصي',
    nav_sales_admin: 'مبيعات JAZ',

    home_title: 'مبيعات JAZ',
    home_welcome: 'مرحباً',
    home_your_roles: 'أدوارك',
    home_super_admin: 'مدير عام - صلاحيات كاملة',
    home_no_role_title: 'لم يتم تعيين دور لك بعد',
    home_no_role_body: 'حسابك مفعّل لكن لا يملك أي دور في مبيعات JAZ. يرجى التواصل مع مدير النظام لتعيين الدور المناسب.',
    home_foundation_title: 'مساحة العمل جاهزة',
    home_foundation_body: 'ستظهر أدوات المبيعات المتاحة لدورك هنا فور تفعيلها.',
    home_available_sections: 'الأقسام المتاحة لك',
    home_access_error: 'تعذر تحميل صلاحياتك',

    no_access_title: 'لا تملك صلاحية الوصول إلى هذا القسم',
    no_access_body: 'تواصل مع مدير النظام إذا كنت تحتاج إلى هذه الصلاحية.',

    team_title: 'فريق المبيعات',
    team_view_only: 'عرض فقط - إدارة الحسابات متاحة للمدير العام',
    team_empty: 'لا يوجد أعضاء في الفريق بعد',
    team_add: 'إضافة موظف',
    team_col_name: 'الاسم',
    team_col_email: 'البريد الإلكتروني',
    team_col_phone: 'الهاتف',
    team_col_roles: 'الأدوار',
    team_col_status: 'الحالة',
    team_col_actions: 'إجراءات',
    team_no_roles: 'بدون دور',
    status_active: 'نشط',
    status_inactive: 'معطّل',

    action_edit: 'تعديل',
    action_roles: 'الأدوار',
    action_reset_password: 'إعادة تعيين كلمة المرور',
    action_deactivate: 'تعطيل',
    action_activate: 'تفعيل',
    action_save: 'حفظ',
    action_create: 'إنشاء الحساب',
    action_cancel: 'إلغاء',
    action_close: 'إغلاق',
    action_retry: 'إعادة المحاولة',

    field_name: 'الاسم',
    field_email: 'البريد الإلكتروني',
    field_phone: 'الهاتف',
    field_password: 'كلمة المرور',
    field_new_password: 'كلمة المرور الجديدة',
    field_initial_roles: 'الأدوار الأولية',

    dlg_create_title: 'إضافة موظف داخلي',
    dlg_edit_title: 'تعديل بيانات الموظف',
    dlg_roles_title: 'أدوار الموظف',
    dlg_roles_hint: 'تُطبَّق التغييرات فوراً. صلاحيات الموظف هي مجموع صلاحيات جميع أدواره.',
    dlg_reset_title: 'إعادة تعيين كلمة المرور',
    dlg_reset_hint: 'سيتم تسجيل خروج الموظف من جميع الأجهزة.',
    confirm_deactivate: 'سيتم منع الموظف من الوصول إلى مبيعات JAZ وتسجيل خروجه. هل أنت متأكد؟',
    confirm_activate: 'إعادة تفعيل هذا الحساب؟',

    toast_created: 'تم إنشاء الحساب',
    toast_updated: 'تم التحديث',
    toast_password_reset: 'تمت إعادة تعيين كلمة المرور',
    toast_role_granted: 'تم تعيين الدور',
    toast_role_revoked: 'تم سحب الدور',
    toast_role_unchanged: 'لا تغيير - الدور مُعيَّن مسبقاً أو غير مُعيَّن',
    toast_error: 'حدث خطأ',
    loading: 'جارٍ التحميل...',
  },
  en: {
    brand: 'JAZ Sales',
    nav_home: 'Sales Dashboard',
    nav_team: 'Team',
    nav_profile: 'Profile',
    nav_sales_admin: 'JAZ Sales',

    home_title: 'JAZ Sales',
    home_welcome: 'Welcome',
    home_your_roles: 'Your roles',
    home_super_admin: 'Super Admin - full access',
    home_no_role_title: 'No role assigned yet',
    home_no_role_body: 'Your account is active but has no JAZ Sales role. Please contact a system administrator to be assigned the right role.',
    home_foundation_title: 'Your workspace is ready',
    home_foundation_body: 'The Sales tools available to your role will appear here as they are enabled.',
    home_available_sections: 'Sections available to you',
    home_access_error: 'Could not load your permissions',

    no_access_title: "You don't have access to this section",
    no_access_body: 'Contact a system administrator if you need this permission.',

    team_title: 'Sales Team',
    team_view_only: 'View only - account management is available to Super Admin',
    team_empty: 'No team members yet',
    team_add: 'Add staff member',
    team_col_name: 'Name',
    team_col_email: 'Email',
    team_col_phone: 'Phone',
    team_col_roles: 'Roles',
    team_col_status: 'Status',
    team_col_actions: 'Actions',
    team_no_roles: 'No role',
    status_active: 'Active',
    status_inactive: 'Deactivated',

    action_edit: 'Edit',
    action_roles: 'Roles',
    action_reset_password: 'Reset password',
    action_deactivate: 'Deactivate',
    action_activate: 'Activate',
    action_save: 'Save',
    action_create: 'Create account',
    action_cancel: 'Cancel',
    action_close: 'Close',
    action_retry: 'Retry',

    field_name: 'Name',
    field_email: 'Email',
    field_phone: 'Phone',
    field_password: 'Password',
    field_new_password: 'New password',
    field_initial_roles: 'Initial roles',

    dlg_create_title: 'Add internal staff member',
    dlg_edit_title: 'Edit staff member',
    dlg_roles_title: 'Staff roles',
    dlg_roles_hint: 'Changes apply immediately. A staff member\'s permissions are the union of all their roles.',
    dlg_reset_title: 'Reset password',
    dlg_reset_hint: 'The staff member will be signed out on every device.',
    confirm_deactivate: 'This person will lose access to JAZ Sales and be signed out. Are you sure?',
    confirm_activate: 'Reactivate this account?',

    toast_created: 'Account created',
    toast_updated: 'Updated',
    toast_password_reset: 'Password reset',
    toast_role_granted: 'Role assigned',
    toast_role_revoked: 'Role revoked',
    toast_role_unchanged: 'No change - role was already assigned / not assigned',
    toast_error: 'Something went wrong',
    loading: 'Loading...',
  },
};

export const salesTranslations = {
  ar: { ...baseTranslations.ar, ...leadsTranslations.ar, ...activitiesTranslations.ar, ...customersTranslations.ar, ...reportsTranslations.ar },
  en: { ...baseTranslations.en, ...leadsTranslations.en, ...activitiesTranslations.en, ...customersTranslations.en, ...reportsTranslations.en },
};

export const ts = (key, language = 'ar') =>
  (salesTranslations[language] && salesTranslations[language][key]) || salesTranslations.ar[key] || key;

// Role display name in the active language (server returns both).
export const roleName = (role, language = 'ar') => (language === 'ar' ? role.name_ar : role.name_en) || role.key;
