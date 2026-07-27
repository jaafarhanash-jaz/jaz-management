import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import LoginPage from './pages/LoginPage';
import SubscriptionBlocked from './pages/SubscriptionBlocked';
import api from './utils/api';
import SuperAdminDashboard from './pages/SuperAdmin/Dashboard';
import SuperAdminCompanies from './pages/SuperAdmin/Companies';
import SuperAdminPlans from './pages/SuperAdmin/Plans';
import OwnerDashboard from './pages/Owner/Dashboard';
import OwnerEmployees from './pages/Owner/Employees';
import OwnerTasks from './pages/Owner/Tasks';
import OwnerAttendance from './pages/Owner/Attendance';
import OwnerReports from './pages/Owner/Reports';
import OwnerDepartments from './pages/Owner/Departments';
import OwnerSubscription from './pages/Owner/Subscription';
import CommunicationCenter from './pages/Owner/CommunicationCenter';
import CalendarMonitor from './pages/Owner/CalendarMonitor';
import CompanyHolidays from './pages/Owner/CompanyHolidays';
import WorkMessages from './pages/WorkMessages';
import CalendarPage from './pages/Calendar';
import Announcements from './pages/Announcements';
import EmployeeDashboard from './pages/Employee/Dashboard';
import EmployeeTasks from './pages/Employee/Tasks';
import EmployeeAttendance from './pages/Employee/Attendance';
import EmployeePerformance from './pages/Employee/Performance';
import EmployeeReports from './pages/Employee/Reports';
import Profile from './pages/Profile';
import { Toaster } from 'sonner';
import CriticalTaskAlert from './components/CriticalTaskAlert';
import '@/App.css';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [userRole, setUserRole] = useState(null);
  // Preferred Language (Profile/Account Settings) - persisted so the choice
  // survives a reload instead of resetting to Arabic every time.
  const [language, setLanguage] = useState(() => localStorage.getItem('language') || 'ar');
  const [pendingCriticalTasks, setPendingCriticalTasks] = useState([]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    const role = localStorage.getItem('role');
    if (token && role) {
      setIsAuthenticated(true);
      setUserRole(role);
    }
  }, []);

  // Update HTML dir attribute when language changes
  useEffect(() => {
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.lang = language;
    localStorage.setItem('language', language);
  }, [language]);

  // Lightweight presence heartbeat for owner/employee sessions (not super_admin -
  // company presence has no meaning for the platform admin). If the company
  // becomes expired/suspended, the heartbeat call itself gets caught by the
  // api.js response interceptor, which logs out and redirects immediately.
  useEffect(() => {
    if (!isAuthenticated || userRole === 'super_admin') return;
    const sendHeartbeat = () => {
      api.post('/heartbeat')
        .then((res) => {
          // Only present for employee sessions; undefined for owner/super_admin.
          if (res.data.pending_critical_tasks) {
            // Keep referential identity when the list is unchanged. A new
            // array object here every 20s re-renders App, and because
            // ProtectedRoute is defined inside App, a re-render REMOUNTS the
            // entire page tree (new component identity) - which tore down
            // the notifications SSE connection and rebuilt the whole DOM on
            // every heartbeat for employee sessions.
            setPendingCriticalTasks((prev) => {
              const next = res.data.pending_critical_tasks;
              return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
            });
          }
        })
        .catch(() => {});
    };
    sendHeartbeat();
    const interval = setInterval(sendHeartbeat, 20000);
    return () => clearInterval(interval);
  }, [isAuthenticated, userRole]);

  const handleCriticalTaskHandled = (taskId) => {
    setPendingCriticalTasks((prev) => prev.filter((t) => t.id !== taskId));
  };

  const handleLogin = (token, refreshToken, role) => {
    localStorage.setItem('token', token);
    localStorage.setItem('refresh_token', refreshToken);
    localStorage.setItem('role', role);
    setIsAuthenticated(true);
    setUserRole(role);
  };

  const handleLogout = () => {
    // Best-effort - the refresh token is revoked server-side so it can't be
    // used to mint new sessions later, but logout must still succeed
    // locally even if this call fails (offline, already-expired access
    // token, etc).
    const refreshToken = localStorage.getItem('refresh_token');
    if (refreshToken) {
      api.post('/auth/logout', { refresh_token: refreshToken }).catch(() => {});
    }
    localStorage.removeItem('token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('role');
    setIsAuthenticated(false);
    setUserRole(null);
    setPendingCriticalTasks([]);
  };

  const ProtectedRoute = ({ children, allowedRoles }) => {
    if (!isAuthenticated) {
      return <Navigate to="/" replace />;
    }
    if (allowedRoles && !allowedRoles.includes(userRole)) {
      return <Navigate to="/" replace />;
    }
    return children;
  };

  return (
    <div className="App" dir={language === 'ar' ? 'rtl' : 'ltr'}>
      <BrowserRouter>
        <Routes>
          <Route path="/subscription-blocked" element={<SubscriptionBlocked />} />
          <Route
            path="/"
            element={
              isAuthenticated ? (
                <Navigate to={`/${userRole?.replace('_', '-')}/dashboard`} replace />
              ) : (
                <LoginPage onLogin={handleLogin} />
              )
            }
          />

          {/* Super Admin Routes */}
          <Route
            path="/super-admin/dashboard"
            element={
              <ProtectedRoute allowedRoles={['super_admin']}>
                <SuperAdminDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/companies"
            element={
              <ProtectedRoute allowedRoles={['super_admin']}>
                <SuperAdminCompanies onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/plans"
            element={
              <ProtectedRoute allowedRoles={['super_admin']}>
                <SuperAdminPlans onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/profile"
            element={
              <ProtectedRoute allowedRoles={['super_admin']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="super_admin" />
              </ProtectedRoute>
            }
          />

          {/* Company Owner Routes */}
          <Route
            path="/company-owner/dashboard"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/employees"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerEmployees onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/tasks"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerTasks onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/attendance"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerAttendance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/reports"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerReports onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/departments"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerDepartments onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/subscription"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <OwnerSubscription onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/messages"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <WorkMessages onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/communication-center"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <CommunicationCenter onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/calendar"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <CalendarPage onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/calendar-monitor"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <CalendarMonitor onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/company-holidays"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <CompanyHolidays onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/announcements"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <Announcements onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/profile"
            element={
              <ProtectedRoute allowedRoles={['company_owner']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />

          {/* Employee Routes */}
          <Route
            path="/employee/dashboard"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <EmployeeDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/tasks"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <EmployeeTasks onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/attendance"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <EmployeeAttendance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/performance"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <EmployeePerformance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/reports"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <EmployeeReports onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/messages"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <WorkMessages onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/calendar"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <CalendarPage onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/announcements"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <Announcements onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/profile"
            element={
              <ProtectedRoute allowedRoles={['employee']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
        </Routes>
      </BrowserRouter>
      {isAuthenticated && userRole === 'employee' && (
        <CriticalTaskAlert tasks={pendingCriticalTasks} onHandled={handleCriticalTaskHandled} />
      )}
      <Toaster position="top-center" richColors />
    </div>
  );
}

export default App;