import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { StageBadge } from '@/components/sales/LeadBadges';
import { ts } from '@/utils/salesTranslations';
import { Building2, ExternalLink, TriangleAlert } from 'lucide-react';

// What a lead would collide with, as returned by POST /sales/leads/duplicate-check (or inside a 409 from create/update).
//
//   exact     the same phone / WhatsApp / email / website - or an existing JAZ customer's owner email / phone
//   possible  the same name in the same city, a number that differs only by country code, a customer with the same name
//
// Nothing here merges or deletes anything: it only shows the matches and, when the caller may, lets them continue
// explicitly. `live` renders the same report as a quiet heads-up while the form is still being filled in.
const KIND_STYLES = {
  exact: 'bg-red-100 text-red-800 border-red-200',
  possible: 'bg-amber-100 text-amber-900 border-amber-200',
};

const NUMBER_FIELDS = ['phone', 'whatsapp'];

const reasonText = (match, language) => {
  const text = ts(`dup_r_${match.field}_${match.matched_on}`, language);
  const similar = match.kind === 'possible' && NUMBER_FIELDS.includes(match.field);
  return similar ? `${text} ${ts('dup_similar_number', language)}` : text;
};

const Reasons = ({ matches, language }) => (
  <ul className="flex flex-wrap gap-1.5 mt-1.5">
    {matches.map((m, i) => (
      <li key={`${m.field}-${m.matched_on}-${i}`} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${KIND_STYLES[m.kind] || KIND_STYLES.possible}`}>
        <span className="font-semibold">{ts(`dup_kind_${m.kind}`, language)}</span>
        <span aria-hidden="true">·</span>
        <span>{reasonText(m, language)}</span>
      </li>
    ))}
  </ul>
);

export const hasDuplicates = (report) => !!report && (report.leads?.length > 0 || report.companies?.length > 0);

const DuplicateReport = ({ report, language, live = false, onConfirm, confirmLabelKey = 'dup_confirm_create', busy = false }) => {
  if (!hasDuplicates(report)) return null;
  const exact = report.has_exact;

  return (
    <div
      role={live ? 'status' : 'alert'}
      data-testid={live ? 'duplicate-live' : 'duplicate-report'}
      className={`rounded-md border p-4 ${exact ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}
    >
      <div className="flex items-start gap-3">
        <TriangleAlert className={`w-5 h-5 mt-0.5 shrink-0 ${exact ? 'text-red-600' : 'text-amber-600'}`} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-[#0A0A0A]" data-testid="duplicate-title">
            {ts(exact ? 'dup_title_exact' : 'dup_title_possible', language)}
          </h3>
          <p className="text-sm text-gray-700 mt-0.5">{ts(live ? 'dup_live_hint' : (exact ? 'dup_intro_exact' : 'dup_intro_possible'), language)}</p>

          {report.leads.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{ts('dup_leads', language)}</p>
              <ul className="mt-1 space-y-2">
                {report.leads.map((lead, i) => (
                  <li key={lead.id || `hidden-${i}`} className="rounded-md bg-white/80 border border-gray-200 p-3" data-testid={`duplicate-lead-${lead.id || i}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-[#0A0A0A]">{lead.business_name}</span>
                      {lead.city && <span className="text-sm text-gray-500">{lead.city}</span>}
                      <StageBadge stage={lead.pipeline_stage} language={language} />
                      {lead.archived && <span className="text-xs rounded-full border border-gray-300 px-2 py-0.5 text-gray-600">{ts('dup_archived', language)}</span>}
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      {lead.assigned_to
                        ? ts('dup_owned', language).replace('{name}', lead.assigned_to.name)
                        : lead.assigned ? ts('dup_owned_other', language) : ts('dup_unassigned', language)}
                    </p>
                    <Reasons matches={lead.matches} language={language} />
                    <div className="mt-2">
                      {lead.id ? (
                        <Link to={`/sales/leads/${lead.id}`} target="_blank" rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-sm font-medium text-[#0033A0] hover:underline">
                          {ts('dup_open', language)}<ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
                        </Link>
                      ) : (
                        <span className="text-xs text-gray-500">{ts('dup_no_access', language)}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.companies.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{ts('dup_companies', language)}</p>
              <ul className="mt-1 space-y-2">
                {report.companies.map((company) => (
                  <li key={company.id} className="rounded-md bg-white/80 border border-gray-200 p-3" data-testid={`duplicate-company-${company.id}`}>
                    <div className="flex items-center gap-2">
                      <Building2 className="w-4 h-4 text-gray-500" aria-hidden="true" />
                      <span className="font-medium text-[#0A0A0A]">{company.name}</span>
                    </div>
                    <Reasons matches={company.matches} language={language} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!live && onConfirm && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {report.can_override ? (
                <Button type="button" onClick={onConfirm} disabled={busy} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="duplicate-confirm">
                  {ts(confirmLabelKey, language)}
                </Button>
              ) : (
                <p className="text-sm font-medium text-red-700" data-testid="duplicate-needs-manager">{ts('dup_needs_manager', language)}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default DuplicateReport;
