import {
  CUSTOMER_STATUS_STYLES,
  ONBOARDING_STAGE_STYLES,
  customerStatusName,
  onboardingStageName,
} from '@/utils/salesCustomers';

// Small status pills for customers and onboarding stages. Colour is a hint only: every pill also carries its text.
const BASE = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap';

export const CustomerStatusBadge = ({ status, language }) => (
  <span className={`${BASE} ${CUSTOMER_STATUS_STYLES[status] || CUSTOMER_STATUS_STYLES.active}`} data-testid={`customer-status-${status}`}>
    {customerStatusName(status, language)}
  </span>
);

export const OnboardingStageBadge = ({ stage, language }) => (
  <span className={`${BASE} ${ONBOARDING_STAGE_STYLES[stage] || ONBOARDING_STAGE_STYLES.won}`} data-testid={`onboarding-stage-${stage}`}>
    {onboardingStageName(stage, language)}
  </span>
);
