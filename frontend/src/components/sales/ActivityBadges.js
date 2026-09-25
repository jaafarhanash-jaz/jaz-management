import {
  CALL_RESULT_STYLES,
  DEMO_STATUS_STYLES,
  FOLLOWUP_STATUS_STYLES,
  TRIAL_STATUS_STYLES,
  callResultName,
  demoStatusName,
  followupStatusName,
  trialStatusName,
} from '@/utils/salesActivities';
import { ts } from '@/utils/salesTranslations';

// Small status pills for calls, follow-ups, demos and trials. Colour is a hint only: every pill also carries its text.
const BASE = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap';

export const CallResultBadge = ({ result, language }) => (
  <span className={`${BASE} ${CALL_RESULT_STYLES[result] || CALL_RESULT_STYLES.no_answer}`} data-testid={`call-result-${result}`}>
    {callResultName(result, language)}
  </span>
);

export const FollowupStatusBadge = ({ status, language }) => (
  <span className={`${BASE} ${FOLLOWUP_STATUS_STYLES[status] || FOLLOWUP_STATUS_STYLES.pending}`} data-testid={`followup-status-${status}`}>
    {followupStatusName(status, language)}
  </span>
);

export const DemoStatusBadge = ({ status, language }) => (
  <span className={`${BASE} ${DEMO_STATUS_STYLES[status] || DEMO_STATUS_STYLES.scheduled}`} data-testid={`demo-status-${status}`}>
    {demoStatusName(status, language)}
  </span>
);

export const TrialStatusBadge = ({ status, language }) => (
  <span className={`${BASE} ${TRIAL_STATUS_STYLES[status] || TRIAL_STATUS_STYLES.active}`} data-testid={`trial-status-${status}`}>
    {trialStatusName(status, language)}
  </span>
);

// "Overdue" / "Past due" / "Ending soon": the clock-derived warnings. `tone` red = something is late, amber = soon.
const FLAG_TONES = { red: 'bg-red-50 text-red-700 border-red-200', amber: 'bg-amber-50 text-amber-800 border-amber-200' };

export const FlagBadge = ({ labelKey, tone = 'red', language, testid }) => (
  <span className={`${BASE} ${FLAG_TONES[tone]}`} data-testid={testid}>{ts(labelKey, language)}</span>
);
