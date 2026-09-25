import {
  CAMPAIGN_STATUS_STYLES,
  PRIORITY_STYLES,
  STAGE_STYLES,
  campaignStatusName,
  priorityName,
  stageName,
} from '@/utils/salesLeads';

// Small status pills. Colour is a hint only: every pill also carries its text label.
const BASE = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap';

export const StageBadge = ({ stage, language }) => (
  <span className={`${BASE} ${STAGE_STYLES[stage] || STAGE_STYLES.new}`} data-testid={`stage-badge-${stage}`}>
    {stageName(stage, language)}
  </span>
);

export const PriorityBadge = ({ priority, language }) => (
  <span className={`${BASE} ${PRIORITY_STYLES[priority] || PRIORITY_STYLES.medium}`} data-testid={`priority-badge-${priority}`}>
    {priorityName(priority, language)}
  </span>
);

export const CampaignStatusBadge = ({ status, language }) => (
  <span className={`${BASE} ${CAMPAIGN_STATUS_STYLES[status] || CAMPAIGN_STATUS_STYLES.active}`} data-testid={`campaign-status-${status}`}>
    {campaignStatusName(status, language)}
  </span>
);

export const Pill = ({ children, className = '', ...props }) => (
  <span className={`${BASE} bg-gray-50 text-gray-700 border-gray-200 ${className}`} {...props}>{children}</span>
);
