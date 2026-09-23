import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Briefcase, LayoutDashboard, UserCircle, Users } from 'lucide-react';
import api from '@/utils/api';
import { ts } from '@/utils/salesTranslations';

// The signed-in user's JAZ Sales access, loaded once from GET /api/sales/me.
//
// IMPORTANT: this only decides what to SHOW. It is not security - every Sales
// endpoint enforces its own permission on the server (a hidden button never
// grants or removes any capability).

const SalesAccessContext = createContext(null);

// Every workspace section the UI knows about. `key` matches the `modules`
// list returned by the server (which already filters by the user's
// permissions); later phases append their sections here.
export const SALES_SECTIONS = [
  { key: 'home', path: '/sales/dashboard', icon: LayoutDashboard, labelKey: 'nav_home' },
  { key: 'team', path: '/sales/team', icon: Users, labelKey: 'nav_team' },
];

export const SalesAccessProvider = ({ children }) => {
  const [state, setState] = useState({ loading: true, error: false, me: null });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: false }));
    try {
      const res = await api.get('/sales/me');
      setState({ loading: false, error: false, me: res.data });
    } catch (e) {
      setState({ loading: false, error: true, me: null });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const value = useMemo(() => {
    const permissions = state.me?.permissions || [];
    const modules = state.me?.modules || [];
    return {
      loading: state.loading,
      error: state.error,
      me: state.me,
      reload: load,
      can: (permission) => permissions.includes(permission),
      hasModule: (key) => modules.includes(key),
    };
  }, [state, load]);

  return <SalesAccessContext.Provider value={value}>{children}</SalesAccessContext.Provider>;
};

// Layout route for the whole Sales workspace: one provider (one /sales/me
// call) that stays mounted while the user moves between Sales pages. Module
// scope on purpose - a component defined inside App() would get a new
// identity on every App render and remount the whole page tree.
export const SalesWorkspace = () => (
  <SalesAccessProvider>
    <Outlet />
  </SalesAccessProvider>
);

export const useSalesAccess = () => {
  const ctx = useContext(SalesAccessContext);
  if (!ctx) throw new Error('useSalesAccess must be used inside <SalesAccessProvider>');
  return ctx;
};

// Sidebar entries for the Sales workspace: the sections the server says this
// user may see, plus Profile (self-service, needs no Sales permission).
// Returns null outside the Sales workspace so Layout falls back to its
// normal role-based menu.
export const useSalesMenu = (language) => {
  const ctx = useContext(SalesAccessContext);
  return useMemo(() => {
    if (!ctx) return null;
    const sections = SALES_SECTIONS.filter((s) => ctx.hasModule(s.key)).map((s) => ({
      icon: s.icon,
      label: ts(s.labelKey, language),
      path: s.path,
    }));
    return [...sections, { icon: UserCircle, label: ts('nav_profile', language), path: '/sales/profile' }];
  }, [ctx, language]);
};

// The entry Super Admin sees in the normal (super_admin) sidebar.
export const superAdminSalesMenuItem = (language) => ({
  icon: Briefcase,
  label: ts('nav_sales_admin', language),
  path: '/sales/dashboard',
});
