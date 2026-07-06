export const translations = {
  ar: {
    // Common
    login: 'تسجيل الدخول',
    logout: 'تسجيل الخروج',
    dashboard: 'لوحة التحكم',
    employees: 'الموظفين',
    tasks: 'المهام',
    attendance: 'الحضور والانصراف',
    reports: 'التقارير',
    departments: 'الأقسام',
    companies: 'الشركات',
    plans: 'خطط الاشتراك',
    performance: 'الأداء',
    notifications: 'الإشعارات',
    profile: 'الملف الشخصي',
    settings: 'الإعدادات',
    
    // Login
    emailOrPhone: 'البريد الإلكتروني أو رقم الهاتف',
    password: 'كلمة المرور',
    welcome: 'مرحباً بك في JAZ',
    loginSubtitle: 'قم بتسجيل الدخول للوصول إلى حسابك',
    
    // Stats
    totalEmployees: 'إجمالي الموظفين',
    presentToday: 'الحضور اليوم',
    lateToday: 'المتأخرين اليوم',
    absentToday: 'الغائبين اليوم',
    openTasks: 'المهام المفتوحة',
    completedTasks: 'المهام المكتملة',
    overdueTasks: 'المهام المتأخرة',
    totalCompanies: 'إجمالي الشركات',
    activeCompanies: 'الشركات النشطة',
    totalRevenue: 'إجمالي الإيرادات',
    
    // Actions
    add: 'إضافة',
    edit: 'تعديل',
    delete: 'حذف',
    save: 'حفظ',
    cancel: 'إلغاء',
    submit: 'إرسال',
    search: 'بحث',
    filter: 'تصفية',
    export: 'تصدير',
    import: 'استيراد',
    
    // Status
    active: 'نشط',
    inactive: 'غير نشط',
    pending: 'قيد الانتظار',
    approved: 'موافق عليه',
    rejected: 'مرفوض',
    new: 'جديد',
    inProgress: 'قيد التنفيذ',
    completed: 'مكتمل',
    overdue: 'متأخر',
    
    // Priority
    high: 'عالية',
    medium: 'متوسطة',
    low: 'منخفضة',
    
    // Time
    today: 'اليوم',
    yesterday: 'أمس',
    thisWeek: 'هذا الأسبوع',
    thisMonth: 'هذا الشهر',
    
    // Messages
    successLogin: 'تم تسجيل الدخول بنجاح',
    errorLogin: 'فشل تسجيل الدخول',
    successAdd: 'تمت الإضافة بنجاح',
    successUpdate: 'تم التحديث بنجاح',
    successDelete: 'تم الحذف بنجاح',
    errorGeneral: 'حدث خطأ ما',
  },
  en: {
    // Common
    login: 'Login',
    logout: 'Logout',
    dashboard: 'Dashboard',
    employees: 'Employees',
    tasks: 'Tasks',
    attendance: 'Attendance',
    reports: 'Reports',
    departments: 'Departments',
    companies: 'Companies',
    plans: 'Subscription Plans',
    performance: 'Performance',
    notifications: 'Notifications',
    profile: 'Profile',
    settings: 'Settings',
    
    // Login
    emailOrPhone: 'Email or Phone',
    password: 'Password',
    welcome: 'Welcome to JAZ',
    loginSubtitle: 'Sign in to access your account',
    
    // Stats
    totalEmployees: 'Total Employees',
    presentToday: 'Present Today',
    lateToday: 'Late Today',
    absentToday: 'Absent Today',
    openTasks: 'Open Tasks',
    completedTasks: 'Completed Tasks',
    overdueTasks: 'Overdue Tasks',
    totalCompanies: 'Total Companies',
    activeCompanies: 'Active Companies',
    totalRevenue: 'Total Revenue',
    
    // Actions
    add: 'Add',
    edit: 'Edit',
    delete: 'Delete',
    save: 'Save',
    cancel: 'Cancel',
    submit: 'Submit',
    search: 'Search',
    filter: 'Filter',
    export: 'Export',
    import: 'Import',
    
    // Status
    active: 'Active',
    inactive: 'Inactive',
    pending: 'Pending',
    approved: 'Approved',
    rejected: 'Rejected',
    new: 'New',
    inProgress: 'In Progress',
    completed: 'Completed',
    overdue: 'Overdue',
    
    // Priority
    high: 'High',
    medium: 'Medium',
    low: 'Low',
    
    // Time
    today: 'Today',
    yesterday: 'Yesterday',
    thisWeek: 'This Week',
    thisMonth: 'This Month',
    
    // Messages
    successLogin: 'Login successful',
    errorLogin: 'Login failed',
    successAdd: 'Added successfully',
    successUpdate: 'Updated successfully',
    successDelete: 'Deleted successfully',
    errorGeneral: 'Something went wrong',
  },
};

export const t = (key, lang = 'ar') => {
  return translations[lang][key] || key;
};