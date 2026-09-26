import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Activity, BarChart3, Briefcase, Building2, ClipboardCheck, Columns3, FlaskConical, Layers, LayoutDashboard, ListChecks, Megaphone, PhoneCall, Presentation, Target, UserCircle, Users } from 'lucide-react';
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
//
// `legacy: true` marks a section that is NOT part of the current MVP (the simplified small-team workflow). Such a section is never
// listed - not in the sidebar, not on the home page. Its route, its page and its API stay exactly as they were, and the page still
// guards itself with its module / permission (the server enforces it too), so an old link or bookmark lands on the same protected
// page as before. Removing the flag lists the section again for whoever holds its permission.
export const SALES_SECTIONS = [
  { key: 'home', path: '/sales/dashboard', icon: LayoutDashboard, labelKey: 'nav_home' },
  { key: 'leads', path: '/sales/leads', icon: Target, labelKey: 'nav_leads' },
  { key: 'pipeline', path: '/sales/pipeline', icon: Columns3, labelKey: 'nav_pipeline', legacy: true },
  { key: 'followups', path: '/sales/followups', icon: ListChecks, labelKey: 'nav_followups', legacy: true },
  { key: 'calls', path: '/sales/calls', icon: PhoneCall, labelKey: 'nav_calls', legacy: true },
  { key: 'demos', path: '/sales/demos', icon: Presentation, labelKey: 'nav_demos', legacy: true },
  { key: 'trials', path: '/sales/trials', icon: FlaskConical, labelKey: 'nav_trials', legacy: true },
  { key: 'customers', path: '/sales/customers', icon: Building2, labelKey: 'nav_customers', legacy: true },
  { key: 'onboarding', path: '/sales/onboarding', icon: ClipboardCheck, labelKey: 'nav_onboarding', legacy: true },
  { key: 'campaigns', path: '/sales/campaigns', icon: Megaphone, labelKey: 'nav_campaigns', legacy: true },
  { key: 'reports', path: '/sales/reports', icon: BarChart3, labelKey: 'nav_reports', legacy: true },   // the Performance page replaces the legacy reports
  { key: 'batches', path: '/sales/batches', icon: Layers, labelKey: 'nav_batches' },           // the Sales Manager's alone (f2b6d8a1c4e9)
  { key: 'performance', path: '/sales/performance', icon: Activity, labelKey: 'nav_performance' },
  { key: 'team', path: '/sales/team', icon: Users, labelKey: 'nav_team' },
];

// What the landing page ("Sales Dashboard") holds for this person: their lead QUEUE (a Sales Employee - leads assigned to them, no
// team-wide scope), the team DASHBOARD (a Sales Manager / Super Admin), or NOTHING (Data Entry works in Leads). Presentation only:
// the server decides what each of them may actually see.
export const salesHomeKind = (can) => {
  if (can('sales.leads.view') && can('sales.leads.scope_assigned') && !can('sales.leads.scope_all')) return 'queue';
  if (can('sales.dashboard.view')) return 'dashboard';
  return 'none';
};

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

// Sidebar entries for the Sales workspace: the current-MVP sections the server
// says this user may see (never a `legacy` one), plus Profile (self-service,
// needs no Sales permission). The landing page is listed when it holds
// something for them (the queue or the dashboard) - or when it is all there is,
// so the menu is never just "Profile". Returns null outside the Sales
// workspace so Layout falls back to its normal role-based menu. Only the NAME
// differs by role: a Sales Employee's landing page is their lead queue, so the
// entry reads "Lead Queue" for them; "Sales Dashboard" stays the Manager's.
export const useSalesMenu = (language) => {
  const ctx = useContext(SalesAccessContext);
  return useMemo(() => {
    if (!ctx) return null;
    const visible = SALES_SECTIONS.filter((s) => !s.legacy && ctx.hasModule(s.key));
    const homeKind = salesHomeKind(ctx.can);
    const sections = visible
      .filter((s) => s.key !== 'home' || homeKind !== 'none' || visible.length === 1)
      .map((s) => ({
        icon: s.icon,
        label: ts(s.key === 'home' && homeKind === 'queue' ? 'nav_queue' : s.labelKey, language),
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
