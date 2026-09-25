// The label / value row of the Sales detail cards (lead, customer, onboarding): an optional icon, a small label, the value.
export const Row = ({ icon: Icon, label, children, testid }) => (
  <div className="flex items-start gap-3 py-2" data-testid={testid}>
    {Icon && <Icon className="w-4 h-4 mt-0.5 text-gray-400 shrink-0" aria-hidden="true" />}
    <div className="min-w-0">
      <p className="text-xs text-gray-500">{label}</p>
      <div className="text-sm text-[#0A0A0A] break-words">{children}</div>
    </div>
  </div>
);

export const Empty = () => <span className="text-gray-400">-</span>;
// A value that keeps its own left-to-right direction inside an Arabic layout (phone numbers, emails).
export const Ltr = ({ children }) => <bdi dir="ltr">{children}</bdi>;
