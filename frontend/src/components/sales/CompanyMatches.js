import { ts } from '@/utils/salesTranslations';

const KIND_STYLES = { exact: 'bg-red-100 text-red-800 border-red-200', possible: 'bg-amber-100 text-amber-900 border-amber-200' };

// The JAZ companies a customer setup could duplicate, each with what matched (exact / possible, and on which field). Used by
// the Customer Setup wizard (CustomerSetupWizard.js).
export const CompanyMatches = ({ duplicates, language }) => (
  <ul className="mt-2 space-y-2" data-testid="conv-duplicate-list">
    {duplicates.companies.map((company) => (
      <li key={company.id} className="rounded-md bg-white/70 border border-gray-200 p-2.5" data-testid={`conv-duplicate-${company.id}`}>
        <p className="text-sm font-medium text-[#0A0A0A]" dir="auto">{company.name}</p>
        <ul className="flex flex-wrap gap-1.5 mt-1">
          {company.matches.map((m, i) => (
            <li key={`${m.field}-${m.matched_on}-${i}`} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${KIND_STYLES[m.kind] || KIND_STYLES.possible}`}>
              <span className="font-semibold">{ts(`dup_kind_${m.kind}`, language)}</span>
              <span aria-hidden="true">·</span>
              <span>{ts(`dup_r_${m.field}_${m.matched_on}`, language)}</span>
            </li>
          ))}
        </ul>
      </li>
    ))}
  </ul>
);
