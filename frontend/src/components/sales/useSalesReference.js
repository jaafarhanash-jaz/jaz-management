import { useEffect, useState } from 'react';
import api from '@/utils/api';

// The reference data the lead screens need for filters and pickers - each part loaded only when the caller's
// permissions make the endpoint available (the server enforces the same rules; this just avoids pointless 403s):
//   sources    - by default (fixed, bilingual list; needs the lead view permission, so the pages of callers who
//                cannot view leads - onboarding - ask for `sources: false`)
//   campaigns  - sales.campaigns.view
//   assignees  - sales.leads.assign (active staff who can receive leads, with their open workload)
//   onboardingAssignees - sales.onboarding.assign (active staff who can be given an onboarding)
export const useSalesReference = ({ sources: wantSources = true, campaigns = false, assignees = false, onboardingAssignees = false } = {}) => {
  const [state, setState] = useState({ sources: [], campaigns: [], assignees: [], onboardingAssignees: [], loaded: false });

  useEffect(() => {
    let cancelled = false;
    const safe = (promise, pick, fallback) => promise.then((res) => pick(res.data)).catch(() => fallback);
    Promise.all([
      wantSources ? safe(api.get('/sales/sources'), (d) => d, []) : Promise.resolve([]),
      campaigns ? safe(api.get('/sales/campaigns', { params: { limit: 200 } }), (d) => d.items, []) : Promise.resolve([]),
      assignees ? safe(api.get('/sales/assignees'), (d) => d, []) : Promise.resolve([]),
      onboardingAssignees ? safe(api.get('/sales/onboarding/assignees'), (d) => d, []) : Promise.resolve([]),
    ]).then(([sources, campaignItems, assigneeItems, onboardingItems]) => {
      if (!cancelled) setState({ sources, campaigns: campaignItems, assignees: assigneeItems, onboardingAssignees: onboardingItems, loaded: true });
    });
    return () => { cancelled = true; };
  }, [wantSources, campaigns, assignees, onboardingAssignees]);

  return state;
};
