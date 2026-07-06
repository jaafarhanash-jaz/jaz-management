import { Link, useLocation } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import {
  LayoutDashboard,
  Users,
  CheckSquare,
  Clock,
  FileText,
  Building2,
  CreditCard,
  LogOut,
  Menu,
  X,
} from 'lucide-react';import { useState } from 'react';
import { t } from '@/utils/translations';

export const Layout = ({ children, userRole, onLogout, language, setLanguage }) => {
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const getMenuItems = () => {
    if (userRole === 'super_admin') {
      return [
        { icon: LayoutDashboard, label: t('dashboard', language), path: '/super-admin/dashboard' },
        { icon: Building2, label: t('companies', language), path: '/super-admin/companies' },
        { icon: CreditCard, label: t('plans', language), path: '/super-admin/plans' },
      ];
    } else if (userRole === 'company_owner') {
      return [
        { icon: LayoutDashboard, label: t('dashboard', language), path: '/company-owner/dashboard' },
        { icon: Users, label: t('employees', language), path: '/company-owner/employees' },
        { icon: CheckSquare, label: t('tasks', language), path: '/company-owner/tasks' },
        { icon: Clock, label: t('attendance', language), path: '/company-owner/attendance' },
        { icon: FileText, label: t('reports', language), path: '/company-owner/reports' },
        { icon: Building2, label: t('departments', language), path: '/company-owner/departments' },
        { icon: CreditCard, label: 'الاشتراك', path: '/company-owner/subscription' },
      ];
    } else if (userRole === 'employee') {
      return [
        { icon: LayoutDashboard, label: t('dashboard', language), path: '/employee/dashboard' },
        { icon: CheckSquare, label: t('tasks', language), path: '/employee/tasks' },
        { icon: Clock, label: t('attendance', language), path: '/employee/attendance' },
        { icon: FileText, label: t('performance', language), path: '/employee/performance' },
        { icon: FileText, label: t('reports', language), path: '/employee/reports' },
      ];
    }
    return [];
  };

  const menuItems = getMenuItems();

  return (
    <div className="flex min-h-screen bg-[#F4F4F5]">
      {/* Sidebar */}
      <aside
        className={`fixed lg:static inset-y-0 ${language === 'ar' ? 'right-0' : 'left-0'} z-50 w-64 bg-white border-${language === 'ar' ? 'l' : 'r'} border-gray-200 transform transition-transform duration-200 lg:transform-none ${
          sidebarOpen ? 'translate-x-0' : language === 'ar' ? 'translate-x-full lg:translate-x-0' : '-translate-x-full lg:translate-x-0'
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-16 px-6 border-b border-gray-200">
            <h1 className="text-2xl font-bold text-[#0033A0]" data-testid="app-logo">JAZ</h1>
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden"
              data-testid="close-sidebar-btn"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          {/* Menu Items */}
          <nav className="flex-1 px-4 py-6 space-y-1 overflow-y-auto">
            {menuItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                  className={`flex items-center gap-3 px-4 py-3 rounded-sm transition-colors ${
                    isActive
                      ? 'bg-[#0033A0] text-white'
                      : 'text-gray-700 hover:bg-gray-100'
                  }`}
                  onClick={() => setSidebarOpen(false)}
                >
                  <Icon className={`w-5 h-5 ${language === 'ar' ? 'flip-rtl' : ''}`} />
                  <span className="font-medium text-sm">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          {/* Logout Button */}
          <div className="p-4 border-t border-gray-200">
            <Button
              onClick={onLogout}
              data-testid="logout-btn"
              variant="ghost"
              className="w-full justify-start gap-3 text-gray-700 hover:text-red-600 hover:bg-red-50"
            >
              <LogOut className={`w-5 h-5 ${language === 'ar' ? 'flip-rtl' : ''}`} />
              <span>{t('logout', language)}</span>
            </Button>
          </div>
        </div>
      </aside>

      {/* Overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="sticky top-0 z-30 bg-white/90 backdrop-blur-xl border-b border-gray-200 h-16">
          <div className="flex items-center justify-between h-full px-6">
            <button
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden"
              data-testid="open-sidebar-btn"
            >
              <Menu className="w-6 h-6" />
            </button>
            
            <div className="flex items-center gap-4">
              <Button
                onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}
                data-testid="language-toggle-btn"
                variant="outline"
                size="sm"
                className="text-xs"
              >
                {language === 'ar' ? 'English' : 'العربية'}
              </Button>
            </div>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-6 md:p-8 lg:p-12">{children}</main>
      </div>
    </div>
  );
};