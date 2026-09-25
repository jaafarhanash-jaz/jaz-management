import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect, lazy, Suspense } from 'react';
import LoginPage from './pages/LoginPage';
import SubscriptionBlocked from './pages/SubscriptionBlocked';
import api from './utils/api';
import { Toaster } from 'sonner';
import CriticalTaskAlert from './components/CriticalTaskAlert';
import { SalesWorkspace } from './components/sales/SalesAccess';
import '@/App.css';

// Route-based code splitting: previously every one of these ~24 page
// components (super_admin + company_owner + employee page trees, all of
// them) was a plain top-level import, so all of it landed in one 732KB
// gzipped main bundle regardless of which single role/page a given visit
// actually needs - a company_owner's browser was downloading and parsing
// the entire employee and super_admin UI (and vice versa) before they
// could even see the login page. React.lazy + the Suspense boundary below
// makes each page its own chunk, fetched only when its route is actually
// visited. Same components, same props, same behavior - just fetched on
// demand instead of all upfront. LoginPage/SubscriptionBlocked stay eager
// since one of them is what nearly every fresh visit renders first, and a
// lazy chunk there would trade a bundle-size win for a loading flash on
// the single most common page load.
const SuperAdminDashboard = lazy(() => import('./pages/SuperAdmin/Dashboard'));
const SuperAdminCompanies = lazy(() => import('./pages/SuperAdmin/Companies'));
const SuperAdminPlans = lazy(() => import('./pages/SuperAdmin/Plans'));
const SuperAdminCompanyNotifications = lazy(() => import('./pages/SuperAdmin/CompanyNotifications'));
const OwnerDashboard = lazy(() => import('./pages/Owner/Dashboard'));
const OwnerEmployees = lazy(() => import('./pages/Owner/Employees'));
const OwnerTasks = lazy(() => import('./pages/Owner/Tasks'));
const OwnerAttendance = lazy(() => import('./pages/Owner/Attendance'));
const OwnerReports = lazy(() => import('./pages/Owner/Reports'));
const OwnerDepartments = lazy(() => import('./pages/Owner/Departments'));
const OwnerSubscription = lazy(() => import('./pages/Owner/Subscription'));
const CommunicationCenter = lazy(() => import('./pages/Owner/CommunicationCenter'));
const CalendarMonitor = lazy(() => import('./pages/Owner/CalendarMonitor'));
const CompanyHolidays = lazy(() => import('./pages/Owner/CompanyHolidays'));
const WorkMessages = lazy(() => import('./pages/WorkMessages'));
const CalendarPage = lazy(() => import('./pages/Calendar'));
const Announcements = lazy(() => import('./pages/Announcements'));
const EmployeeDashboard = lazy(() => import('./pages/Employee/Dashboard'));
const EmployeeTasks = lazy(() => import('./pages/Employee/Tasks'));
const EmployeeAttendance = lazy(() => import('./pages/Employee/Attendance'));
const EmployeePerformance = lazy(() => import('./pages/Employee/Performance'));
const EmployeeReports = lazy(() => import('./pages/Employee/Reports'));
const Profile = lazy(() => import('./pages/Profile'));
const SalesHome = lazy(() => import('./pages/Sales/SalesHome'));
const SalesTeam = lazy(() => import('./pages/Sales/Team'));
const SalesLeads = lazy(() => import('./pages/Sales/Leads'));
const SalesLeadEditor = lazy(() => import('./pages/Sales/LeadEditor'));
const SalesLeadDetail = lazy(() => import('./pages/Sales/LeadDetail'));
const SalesPipeline = lazy(() => import('./pages/Sales/Pipeline'));
const SalesCampaigns = lazy(() => import('./pages/Sales/Campaigns'));
const SalesCustomers = lazy(() => import('./pages/Sales/Customers'));
const SalesCustomerDetail = lazy(() => import('./pages/Sales/CustomerDetail'));
const SalesOnboarding = lazy(() => import('./pages/Sales/Onboarding'));
const SalesOnboardingDetail = lazy(() => import('./pages/Sales/OnboardingDetail'));
const SalesFollowups = lazy(() => import('./pages/Sales/Followups'));
const SalesCalls = lazy(() => import('./pages/Sales/Calls'));
const SalesDemos = lazy(() => import('./pages/Sales/Demos'));
const SalesTrials = lazy(() => import('./pages/Sales/Trials'));
const SalesReports = lazy(() => import('./pages/Sales/Reports'));

// Shown only for the brief moment a lazy page chunk is being fetched
// (typically a single-digit-ms cache hit after the first visit to that
// route). Deliberately minimal, not a full skeleton - most page
// components already render their own loading state for their data fetch
// once mounted, so this only covers the JS-chunk-fetch gap before that.
const RouteFallback = () => (
  <div className="flex items-center justify-center min-h-screen">
    <div className="animate-spin rounded-full h-8 w-8 border-2 border-gray-300 border-t-[#0033A0]" />
  </div>
);

// Module-scope (not defined inside App()) so it keeps the same component
// identity across every App re-render - e.g. the language toggle in Layout,
// which every page renders. When this was defined inside App(), each such
// re-render created a brand-new ProtectedRoute function reference, and
// React remounts (not just re-renders) anything whose component identity
// changed - tearing down and rebuilding the entire current page, including
// its notifications SSE connection, on something as small as a language
// click. isAuthenticated/userRole are now passed in as props instead of
// captured via closure so this component has a stable identity.
const ProtectedRoute = ({ children, allowedRoles, isAuthenticated, userRole }) => {
  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }
  if (allowedRoles && !allowedRoles.includes(userRole)) {
    return <Navigate to="/" replace />;
  }
  return children;
};

// The signed-in role, read from localStorage - or null when there is no session. Read SYNCHRONOUSLY, in the state
// initializers below: setting it from an effect (as this used to) left the very first render "unauthenticated", so on a hard
// refresh every protected route redirected to "/" - which then forwarded to the role's home page, dropping the deep link
// (/sales/leads, /company-owner/tasks ... all landed on their dashboard).
const storedRole = () => {
  const token = localStorage.getItem('token');
  const role = localStorage.getItem('role');
  return token && role ? role : null;
};

function App() {
  const [userRole, setUserRole] = useState(storedRole);
  const [isAuthenticated, setIsAuthenticated] = useState(() => storedRole() !== null);
  // Preferred Language (Profile/Account Settings) - persisted so the choice
  // survives a reload instead of resetting to Arabic every time.
  const [language, setLanguage] = useState(() => localStorage.getItem('language') || 'ar');
  const [pendingCriticalTasks, setPendingCriticalTasks] = useState([]);

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

  return (
    <div className="App" dir={language === 'ar' ? 'rtl' : 'ltr'}>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
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
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['super_admin']}>
                <SuperAdminDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/companies"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['super_admin']}>
                <SuperAdminCompanies onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/plans"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['super_admin']}>
                <SuperAdminPlans onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/notifications"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['super_admin']}>
                <SuperAdminCompanyNotifications onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/super-admin/profile"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['super_admin']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="super_admin" />
              </ProtectedRoute>
            }
          />

          {/* Company Owner Routes */}
          <Route
            path="/company-owner/dashboard"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/employees"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerEmployees onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/tasks"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerTasks onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/attendance"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerAttendance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/reports"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerReports onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/departments"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerDepartments onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/subscription"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <OwnerSubscription onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/messages"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <WorkMessages onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/communication-center"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <CommunicationCenter onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/calendar"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <CalendarPage onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/calendar-monitor"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <CalendarMonitor onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/company-holidays"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <CompanyHolidays onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/announcements"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <Announcements onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/company-owner/profile"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['company_owner']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="company_owner" />
              </ProtectedRoute>
            }
          />

          {/* Employee Routes */}
          <Route
            path="/employee/dashboard"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <EmployeeDashboard onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/tasks"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <EmployeeTasks onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/attendance"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <EmployeeAttendance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/performance"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <EmployeePerformance onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/reports"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <EmployeeReports onLogout={handleLogout} language={language} setLanguage={setLanguage} />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/messages"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <WorkMessages onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/calendar"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <CalendarPage onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/announcements"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <Announcements onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/employee/profile"
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['employee']}>
                <Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole="employee" />
              </ProtectedRoute>
            }
          />

          {/* JAZ Sales (internal staff) - only jaz_staff and super_admin get past the
              route guard, but that is UX only: every /api/sales endpoint enforces its
              own permission server-side. One layout route = one /sales/me fetch. */}
          <Route path="/jaz-staff/dashboard" element={<Navigate to="/sales/dashboard" replace />} />
          <Route
            element={
              <ProtectedRoute isAuthenticated={isAuthenticated} userRole={userRole} allowedRoles={['jaz_staff', 'super_admin']}>
                <SalesWorkspace />
              </ProtectedRoute>
            }
          >
            <Route path="/sales/dashboard" element={<SalesHome onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/leads" element={<SalesLeads onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/leads/new" element={<SalesLeadEditor onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/leads/:leadId" element={<SalesLeadDetail onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/leads/:leadId/edit" element={<SalesLeadEditor onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/pipeline" element={<SalesPipeline onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/campaigns" element={<SalesCampaigns onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/followups" element={<SalesFollowups onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/calls" element={<SalesCalls onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/demos" element={<SalesDemos onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/trials" element={<SalesTrials onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/customers" element={<SalesCustomers onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/customers/:customerId" element={<SalesCustomerDetail onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/onboarding" element={<SalesOnboarding onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/onboarding/:onboardingId" element={<SalesOnboardingDetail onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/reports" element={<SalesReports onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/team" element={<SalesTeam onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
            <Route path="/sales/profile" element={<Profile onLogout={handleLogout} language={language} setLanguage={setLanguage} userRole={userRole} />} />
          </Route>
        </Routes>
        </Suspense>
      </BrowserRouter>
      {isAuthenticated && userRole === 'employee' && (
        <CriticalTaskAlert tasks={pendingCriticalTasks} onHandled={handleCriticalTaskHandled} />
      )}
      <Toaster position="top-center" richColors />
    </div>
  );
}

export default App;