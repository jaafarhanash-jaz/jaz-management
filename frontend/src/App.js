import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import LoginPage from './pages/LoginPage';
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
import EmployeeDashboard from './pages/Employee/Dashboard';
import EmployeeTasks from './pages/Employee/Tasks';
import EmployeeAttendance from './pages/Employee/Attendance';
import EmployeePerformance from './pages/Employee/Performance';
import EmployeeReports from './pages/Employee/Reports';
import { Toaster } from 'sonner';
import '@/App.css';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [userRole, setUserRole] = useState(null);
  const [language, setLanguage] = useState('ar');

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
  }, [language]);

  const handleLogin = (token, role) => {
    localStorage.setItem('token', token);
    localStorage.setItem('role', role);
    setIsAuthenticated(true);
    setUserRole(role);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('role');
    setIsAuthenticated(false);
    setUserRole(null);
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
        </Routes>
      </BrowserRouter>
      <Toaster position="top-center" richColors />
    </div>
  );
}

export default App;