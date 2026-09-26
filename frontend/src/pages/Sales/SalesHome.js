import { Link, Navigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { SALES_SECTIONS, salesHomeKind, useSalesAccess } from '@/components/sales/SalesAccess';
import SalesDashboard from '@/components/sales/SalesDashboard';
import MyLeadWorkspace from '@/components/sales/MyLeadWorkspace';
import { ts, roleName } from '@/utils/salesTranslations';
import { ShieldCheck, UserX } from 'lucide-react';

// The Sales landing page, by what the person works with (see salesHomeKind):
//   queue      a Sales Employee - their lead queue (the current lead and its outcomes) under their identity card
//   dashboard  a Sales Manager / Super Admin - the dashboard
//   none       Data Entry and anybody else who may open Leads - there is nothing on this page for them, so they land on Leads;
//              someone with no Leads access sees the sections that are open to them
// Sections outside the current MVP are never offered here (SALES_SECTIONS marks them `legacy`).
const SalesHome = ({ onLogout, language, setLanguage, userRole }) => {
  const { loading, error, me, hasModule, reload, can } = useSalesAccess();

  const noRole = me && !me.is_super_admin && me.roles.length === 0;
  const kind = salesHomeKind(can);
  const otherSections = SALES_SECTIONS.filter((s) => s.key !== 'home' && !s.legacy && hasModule(s.key));

  if (me && !noRole && kind === 'none' && hasModule('leads')) return <Navigate to="/sales/leads" replace />;

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-home-title">{ts('home_title', language)}</h1>

        {loading && <div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>}

        {error && (
          <Card className="p-8 text-center bg-white border border-gray-200" data-testid="sales-home-error">
            <p className="text-gray-600 mb-4">{ts('home_access_error', language)}</p>
            <Button onClick={reload} variant="outline" className="rounded-sm">{ts('action_retry', language)}</Button>
          </Card>
        )}

        {me && (
          <>
            {!noRole && kind === 'queue' && <MyLeadWorkspace language={language} />}

            <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="sales-home-identity">
              <p className="text-sm text-gray-500">{ts('home_welcome', language)}</p>
              <p className="text-xl font-semibold text-[#0A0A0A] mt-1">{me.user.name}</p>

              <div className={`mt-4 ${noRole ? 'hidden' : ''}`}>
                <p className="text-sm text-gray-500 mb-2">{ts('home_your_roles', language)}</p>
                {me.is_super_admin ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border bg-blue-50 text-[#0033A0] border-blue-200" data-testid="sales-home-super-admin">
                    <ShieldCheck className="w-3.5 h-3.5" />{ts('home_super_admin', language)}
                  </span>
                ) : (
                  <div className="flex flex-wrap gap-2" data-testid="sales-home-roles">
                    {me.roles.map((r) => (
                      <span key={r.key} className="px-2.5 py-0.5 rounded-full text-xs font-medium border bg-blue-50 text-[#0033A0] border-blue-200">
                        {roleName(r, language)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </Card>

            {noRole ? (
              <Card className="p-12 text-center bg-white border border-gray-200" data-testid="sales-home-no-role">
                <UserX className="w-12 h-12 mx-auto text-gray-300 mb-4" />
                <h2 className="text-lg font-semibold text-[#0A0A0A]">{ts('home_no_role_title', language)}</h2>
                <p className="text-gray-500 mt-2 max-w-xl mx-auto">{ts('home_no_role_body', language)}</p>
              </Card>
            ) : kind === 'dashboard' ? (
              <SalesDashboard language={language} />
            ) : kind === 'none' && otherSections.length > 0 ? (
              <div data-testid="sales-home-sections">
                <p className="text-sm text-gray-500 mb-2">{ts('home_available_sections', language)}</p>
                <div className="flex flex-wrap gap-3">
                  {otherSections.map((s) => {
                    const Icon = s.icon;
                    return (
                      <Link key={s.key} to={s.path} data-testid={`sales-home-link-${s.key}`}
                        className="flex items-center gap-2 px-4 py-3 bg-white border border-gray-200 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50">
                        <Icon className="w-4 h-4" />{ts(s.labelKey, language)}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Layout>
  );
};

export default SalesHome;
