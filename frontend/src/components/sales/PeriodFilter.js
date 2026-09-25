import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { RANGES, customPeriodProblem, rangeName } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';

// The period picker shared by the dashboard and the reports: a preset (today / this week / this month / last month) applies at
// once; a custom range needs both dates and the Apply button. The server validates again and is the authority - this only
// spares the user a round trip for the obvious mistakes. `value` = { range, from, to }; `onChange` gets the same shape.
const PeriodFilter = ({ value, onChange, language, idPrefix = 'period' }) => {
  const [range, setRange] = useState(value.range);
  const [from, setFrom] = useState(value.from || '');
  const [to, setTo] = useState(value.to || '');
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    setRange(value.range);
    setFrom(value.from || '');
    setTo(value.to || '');
  }, [value.range, value.from, value.to]);

  const pick = (next) => {
    setRange(next);
    setProblem(null);
    if (next !== 'custom') onChange({ range: next, from: '', to: '' });
  };

  const apply = () => {
    const found = customPeriodProblem(from, to);
    setProblem(found);
    if (!found) onChange({ range: 'custom', from, to });
  };

  return (
    <div className="flex flex-wrap items-end gap-3" data-testid={`${idPrefix}-filter`}>
      <div className="min-w-[10rem]">
        <Label htmlFor={`${idPrefix}-range`} className="text-xs text-gray-600">{ts('period_label', language)}</Label>
        <Select value={range} onValueChange={pick}>
          <SelectTrigger id={`${idPrefix}-range`} className="mt-1 h-10" data-testid={`${idPrefix}-range`}><SelectValue /></SelectTrigger>
          <SelectContent>
            {RANGES.map((r) => <SelectItem key={r} value={r}>{rangeName(r, language)}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {range === 'custom' && (
        <>
          <div>
            <Label htmlFor={`${idPrefix}-from`} className="text-xs text-gray-600">{ts('period_from', language)}</Label>
            <Input id={`${idPrefix}-from`} type="date" dir="ltr" className="mt-1 h-10 w-40" value={from} onChange={(e) => setFrom(e.target.value)} data-testid={`${idPrefix}-from`} aria-invalid={!!problem} />
          </div>
          <div>
            <Label htmlFor={`${idPrefix}-to`} className="text-xs text-gray-600">{ts('period_to', language)}</Label>
            <Input id={`${idPrefix}-to`} type="date" dir="ltr" className="mt-1 h-10 w-40" value={to} onChange={(e) => setTo(e.target.value)} data-testid={`${idPrefix}-to`} aria-invalid={!!problem} />
          </div>
          <Button type="button" className="h-10 bg-[#0033A0] hover:bg-[#002277] rounded-sm" onClick={apply} data-testid={`${idPrefix}-apply`}>{ts('period_apply', language)}</Button>
        </>
      )}

      {problem && (
        <p className="basis-full text-sm text-red-700" role="alert" data-testid={`${idPrefix}-problem`}>
          {ts(problem.key, language).replace('{n}', problem.n)}
        </p>
      )}
    </div>
  );
};

export default PeriodFilter;
