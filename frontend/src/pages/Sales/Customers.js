import { TableCell } from '@/components/ui/table';
import ActivityListPage from '@/components/sales/ActivityListPage';
import { CustomerStatusBadge } from '@/components/sales/CustomerBadges';
import { CustomerCard, CustomerName, CustomerOnboardingSummary } from '@/components/sales/CustomerItems';
import { CUSTOMER_STATUSES } from '@/utils/salesCustomers';
import { formatDate } from '@/utils/salesLeads';
import { ts } from '@/utils/salesTranslations';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Eye } from 'lucide-react';

// Customers: the leads that were won and converted into JAZ companies. The tabs ARE the customer-status filter, each with its
// number (GET /customers/counts); the list follows the LEAD scope - a customer is visible exactly when its lead is.
const VIEWS = [
  { key: 'all', labelKey: 'filter_all', emptyKey: 'customers_empty', count: (c) => c.__total, params: {} },
  ...CUSTOMER_STATUSES.map((status) => ({
    key: status, labelKey: `customer_status_${status}`, emptyKey: 'customers_empty_filtered', count: (c) => c[status], params: { status },
  })),
];

const COLUMNS = [
  { labelKey: 'cust_col_customer' },
  { labelKey: 'cust_col_status' },
  { labelKey: 'cust_col_company' },
  { labelKey: 'cust_col_converted', className: 'hidden xl:table-cell' },
  { labelKey: 'cust_col_onboarding' },
  { labelKey: 'cust_col_actions' },
];

const SalesCustomers = ({ onLogout, language, setLanguage, userRole }) => {
  const renderRow = (item) => (
    <>
      <TableCell className="max-w-xs"><CustomerName item={item} /></TableCell>
      <TableCell><CustomerStatusBadge status={item.status} language={language} /></TableCell>
      <TableCell className="text-sm">
        <bdi>{item.company.name}</bdi>
        {item.company.deleted && <span className="block text-xs text-red-600">{ts('cust_company_deleted', language)}</span>}
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap hidden xl:table-cell">
        <bdi>{formatDate(item.converted_at, language)}</bdi>
        {item.converted_by && <span className="block text-xs text-gray-500">{item.converted_by.name}</span>}
      </TableCell>
      <TableCell><CustomerOnboardingSummary item={item} language={language} /></TableCell>
      <TableCell>
        <Button asChild size="sm" variant="outline" className="rounded-sm">
          <Link to={`/sales/customers/${item.id}`} data-testid={`customer-open-${item.id}`}><Eye className="w-3.5 h-3.5 me-1" aria-hidden="true" />{ts('cust_action_open', language)}</Link>
        </Button>
      </TableCell>
    </>
  );

  return (
    <ActivityListPage
      onLogout={onLogout} language={language} setLanguage={setLanguage} userRole={userRole} testid="customers" showPerson={false} dimArchived={false}
      moduleKey="customers" titleKey="customers_title" subtitle="customers_subtitle" listPath="/sales/customers" countsPath="/sales/customers/counts"
      unwrapCounts={(data) => ({ ...data.counts, __total: data.total })} scopeHintKey="cust_scope_hint" searchPlaceholderKey="cust_search_placeholder"
      views={VIEWS} defaultView="all" columns={COLUMNS} renderRow={renderRow} emptyHintKey="customers_empty_hint"
      renderCard={(item) => <CustomerCard item={item} language={language} />}
    />
  );
};

export default SalesCustomers;
