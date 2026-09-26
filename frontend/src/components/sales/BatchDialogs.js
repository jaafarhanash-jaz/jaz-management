import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { formatDateTime } from '@/utils/salesLeads';
import { formatCount } from '@/utils/salesReports';
import { ts } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import { useSalesReference } from '@/components/sales/useSalesReference';
import { DataTable } from '@/components/sales/ReportTables';
import LeadTimeline from '@/components/sales/LeadTimeline';
import { AttemptsList, BatchStatus, StatsGrid, StatsLegend, fill, leadColumns } from '@/components/sales/BatchParts';
import { ArrowRight, Repeat } from 'lucide-react';
import { toast } from 'sonner';

// The Sales Manager's batch detail dialogs. Everything is read from the server (sales.batches.view); the only write here is the
// reassignment of a completed Work Batch (sales.batches.reassign), which the server validates again.

// GET `url` whenever the dialog opens. null = loading, false = failed.
const useDetail = (url, open) => {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!open || !url) return undefined;
    let cancelled = false;
    setData(null);
    api.get(url).then((res) => { if (!cancelled) setData(res.data); }).catch(() => { if (!cancelled) setData(false); });
    return () => { cancelled = true; };
  }, [url, open]);
  return [data, setData];
};

const Body = ({ data, language, children }) => {
  if (data === null) return <p className="py-8 text-center text-sm text-gray-500">{ts('loading', language)}</p>;
  if (data === false) return <p className="py-8 text-center text-sm text-red-600" role="alert">{ts('bt_load_error', language)}</p>;
  return children;
};

// ---- a Data Batch -------------------------------------------------------------------------------------------------------
export const DataBatchDialog = ({ batchId, open, onOpenChange, language }) => {
  const [data] = useDetail(batchId && `/sales/batches/data/${batchId}`, open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto" data-testid="data-batch-dialog">
        <DialogHeader className="text-start sm:text-start pr-8">
          <DialogTitle>{data ? fill(ts('bt_batch_n', language), { n: data.seq }) : ts('bt_tab_data', language)}</DialogTitle>
          {data && (
            <DialogDescription className="flex flex-wrap items-center gap-2">
              <BatchStatus status={data.status} language={language} />
              <span>{formatCount(data.lead_count, language)} / {formatCount(data.capacity, language)}</span>
              {data.master_batch && <span>· {fill(ts('bt_master_n', language), { n: data.master_batch.seq })}</span>}
              <span className="inline-flex items-center gap-1">· {formatDateTime(data.opened_at, language)}
                {data.filled_at && <><ArrowRight className="w-3 h-3 rtl:rotate-180" aria-hidden="true" />{formatDateTime(data.filled_at, language)}</>}
              </span>
            </DialogDescription>
          )}
        </DialogHeader>
        <Body data={data} language={language}>
          {data && (
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-1">{ts('bt_entrants', language)}</h3>
                <ul className="flex flex-wrap gap-2" data-testid="batch-entrants">
                  {data.entrants.map((e) => (
                    <li key={e.id} className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs"><bdi className="font-medium">{e.name}</bdi> · {fill(ts('bt_lead_count_of', language), { n: formatCount(e.leads, language) })}</li>
                  ))}
                </ul>
              </div>
              <DataTable testid="batch-leads" columns={leadColumns(language)} rows={data.leads} rowKey={(l) => l.id} caption={ts('bt_tab_data', language)} />
            </div>
          )}
        </Body>
      </DialogContent>
    </Dialog>
  );
};

// ---- a Master Batch -----------------------------------------------------------------------------------------------------
export const MasterBatchDialog = ({ batchId, open, onOpenChange, language, onOpenData }) => {
  const [data] = useDetail(batchId && `/sales/batches/master/${batchId}`, open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto" data-testid="master-batch-dialog">
        <DialogHeader className="text-start sm:text-start pr-8">
          <DialogTitle>{data ? fill(ts('bt_master_n', language), { n: data.seq }) : ts('bt_tab_master', language)}</DialogTitle>
          {data && <DialogDescription>{formatDateTime(data.formed_at, language)} · {fill(ts('bt_lead_count_of', language), { n: formatCount(data.lead_count, language) })}</DialogDescription>}
        </DialogHeader>
        <Body data={data} language={language}>
          {data && (
            <div>
              <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('bt_master_batches_inside', language)}</h3>
              <DataTable
                testid="master-data-batches"
                rows={data.data_batches}
                rowKey={(b) => b.id}
                caption={ts('bt_col_data_batches', language)}
                columns={[
                  { key: 'seq', label: ts('bt_col_number', language), render: (b) => <button type="button" className="text-[#0033A0] hover:underline font-medium" onClick={() => onOpenData(b.id)}>{fill(ts('bt_batch_n', language), { n: b.seq })}</button> },
                  { key: 'leads', label: ts('bt_col_leads', language), numeric: true, render: (b) => formatCount(b.lead_count, language) },
                  { key: 'opened', label: ts('bt_col_opened', language), render: (b) => formatDateTime(b.opened_at, language) },
                  { key: 'filled', label: ts('bt_col_filled', language), render: (b) => formatDateTime(b.filled_at, language) },
                ]}
              />
            </div>
          )}
        </Body>
      </DialogContent>
    </Dialog>
  );
};

// ---- the reassignment control of a completed Work Batch -----------------------------------------------------------------
const ReassignBox = ({ batch, language, onDone }) => {
  const ref = useSalesReference({ sources: false, assignees: true });
  const [employee, setEmployee] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const options = ref.assignees.filter((a) => a.id !== batch.current_employee.id);
  const chosen = options.find((a) => a.id === employee);

  const submit = async () => {
    if (!chosen || !window.confirm(fill(ts('bt_reassign_confirm', language), { name: chosen.name }))) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.post(`/sales/batches/work/${batch.id}/reassign`, { employee_id: employee });
      toast.success(ts('bt_reassign_done', language));
      setEmployee('');
      onDone(res.data);
    } catch (err) {
      setError(apiErrorMessage(err, language));
    }
    setBusy(false);
  };

  return (
    <section className="rounded-md border border-gray-200 bg-gray-50 p-4 space-y-3" data-testid="reassign-box">
      <h3 className="text-sm font-semibold text-[#0A0A0A] flex items-center gap-2"><Repeat className="w-4 h-4" aria-hidden="true" />{ts('bt_reassign_title', language)}</h3>
      <p className="text-xs text-gray-600">{ts('bt_reassign_hint', language)}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem]">
          <Label htmlFor="reassign-employee" className="text-xs text-gray-600">{ts('bt_reassign_pick', language)}</Label>
          <Select value={employee} onValueChange={setEmployee}>
            <SelectTrigger id="reassign-employee" className="mt-1 h-10 bg-white" data-testid="reassign-employee"><SelectValue placeholder={ts('bt_reassign_pick', language)} /></SelectTrigger>
            <SelectContent>{options.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <Button className="rounded-sm bg-[#0033A0] hover:bg-[#002277]" disabled={!employee || busy} onClick={submit} data-testid="reassign-submit">{ts('bt_reassign_submit', language)}</Button>
      </div>
      {error && <p className="text-sm text-red-600" role="alert" data-testid="reassign-error">{error}</p>}
    </section>
  );
};

// ---- a Work Batch -------------------------------------------------------------------------------------------------------
export const WorkBatchDialog = ({ batchId, open, onOpenChange, language, onChanged }) => {
  const { can } = useSalesAccess();
  const [data, setData] = useDetail(batchId && `/sales/batches/work/${batchId}`, open);
  const reassigned = (fresh) => { setData(fresh); onChanged && onChanged(); };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-y-auto" data-testid="work-batch-dialog">
        <DialogHeader className="text-start sm:text-start pr-8">
          <DialogTitle>{data ? fill(ts('bt_work_n', language), { n: data.seq }) : ts('bt_tab_work', language)}</DialogTitle>
          {data && (
            <DialogDescription className="flex flex-wrap items-center gap-2">
              <BatchStatus status={data.status} language={language} />
              <span>{fill(ts('bt_lead_count_of', language), { n: formatCount(data.lead_count, language) })}</span>
              <span className="inline-flex items-center gap-1">· {formatDateTime(data.created_at, language)}
                {data.closed_at && <><ArrowRight className="w-3 h-3 rtl:rotate-180" aria-hidden="true" />{formatDateTime(data.closed_at, language)}</>}
              </span>
            </DialogDescription>
          )}
        </DialogHeader>
        <Body data={data} language={language}>
          {data && (
            <div className="space-y-5">
              <section aria-label={ts('bt_attempts_title', language)}>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('bt_attempts_title', language)}</h3>
                <ol className="space-y-3" data-testid="batch-attempts">
                  {data.attempts.map((a) => (
                    <li key={a.id} className="rounded-md border border-gray-200 p-3" data-testid={`batch-attempt-${a.attempt_no}`}>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2 text-sm">
                        <span className="font-semibold">{fill(ts('bt_attempt_n', language), { n: a.attempt_no })}</span>
                        <span className="text-xs text-gray-500">{ts(`bt_attempt_${a.kind}`, language)}</span>
                        <BatchStatus status={a.status} language={language} />
                        <bdi className="font-medium">{a.employee.name}</bdi>
                        <span className="text-xs text-gray-500 flex items-center gap-1">
                          {ts('bt_started_by', language)} <bdi>{a.started_by.name}</bdi> · {formatDateTime(a.started_at, language)}
                          {a.closed_at && <><ArrowRight className="w-3 h-3 rtl:rotate-180" aria-hidden="true" />{formatDateTime(a.closed_at, language)}</>}
                        </span>
                      </div>
                      <StatsGrid stats={a.stats} language={language} testid={`attempt-stats-${a.attempt_no}`} />
                    </li>
                  ))}
                </ol>
                <div className="mt-2"><StatsLegend language={language} /></div>
              </section>

              {data.can_reassign && <ReassignBox batch={data} language={language} onDone={reassigned} />}
              {data.status === 'open' && can('sales.batches.reassign') && <p className="text-xs text-gray-500" data-testid="reassign-open-note">{ts('bt_reassign_open_note', language)}</p>}
              {data.status === 'closed' && !data.can_reassign && can('sales.batches.reassign') && <p className="text-xs text-gray-500" data-testid="reassign-none-note">{ts('bt_reassign_none_note', language)}</p>}

              <section aria-label={ts('bt_leads_history', language)}>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('bt_leads_history', language)}</h3>
                <DataTable
                  testid="work-batch-leads"
                  rows={data.leads}
                  rowKey={(l) => l.id}
                  caption={ts('bt_leads_history', language)}
                  columns={[
                    { key: 'ordinal', label: '#', numeric: true, render: (l) => l.ordinal },
                    ...leadColumns(language).filter((c) => ['business', 'contact', 'phone', 'stage', 'owner'].includes(c.key)),
                    { key: 'attempts', label: ts('bt_trace_attempts', language), render: (l) => <AttemptsList attempts={l.attempts} language={language} /> },
                  ]}
                />
              </section>
            </div>
          )}
        </Body>
      </DialogContent>
    </Dialog>
  );
};

// ---- the trace of one lead ----------------------------------------------------------------------------------------------
export const TraceDialog = ({ leadId, open, onOpenChange, language, onOpenData, onOpenMaster, onOpenWork }) => {
  const ref = useSalesReference({});
  const [data] = useDetail(leadId && `/sales/batches/trace/${leadId}`, open);
  const link = (label, onClick) => <button type="button" className="text-[#0033A0] hover:underline font-medium" onClick={onClick}>{label}</button>;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto" data-testid="trace-dialog">
        <DialogHeader className="text-start sm:text-start pr-8">
          <DialogTitle>{ts('bt_trace_title', language)}{data && <>: <bdi>{data.business_name}</bdi></>}</DialogTitle>
          {data && (
            <DialogDescription>
              {[
                data.business_type && <bdi key="type">{data.business_type}</bdi>,
                data.contact_name && <bdi key="contact">{data.contact_name}</bdi>,
                data.phone && <bdi key="phone" dir="ltr">{data.phone}</bdi>,
              ].filter(Boolean).reduce((out, part, i) => (i ? [...out, ' · ', part] : [part]), [])}
            </DialogDescription>
          )}
        </DialogHeader>
        <Body data={data} language={language}>
          {data && (
            <div className="space-y-4">
              <ol className="flex flex-wrap items-center gap-2 text-sm" data-testid="trace-chain">
                <li className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5">
                  <span className="text-xs text-gray-500 block">{ts('bt_entered_by', language)}</span>
                  <bdi className="font-medium">{data.entered_by.name}</bdi> · {formatDateTime(data.entered_at, language)}
                </li>
                <ArrowRight className="w-4 h-4 text-gray-400 rtl:rotate-180" aria-hidden="true" />
                <li className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5" data-testid="trace-data-batch">
                  <span className="text-xs text-gray-500 block">{ts('bt_trace_data_batch', language)}</span>
                  {data.data_batch ? link(fill(ts('bt_batch_n', language), { n: data.data_batch.seq }), () => onOpenData(data.data_batch.id)) : <span className="text-gray-500">{ts('bt_trace_none_batch', language)}</span>}
                </li>
                <ArrowRight className="w-4 h-4 text-gray-400 rtl:rotate-180" aria-hidden="true" />
                <li className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5" data-testid="trace-master-batch">
                  <span className="text-xs text-gray-500 block">{ts('bt_trace_master_batch', language)}</span>
                  {data.master_batch ? link(fill(ts('bt_master_n', language), { n: data.master_batch.seq }), () => onOpenMaster(data.master_batch.id)) : <span className="text-gray-500">{ts('bt_trace_none_master', language)}</span>}
                </li>
                <ArrowRight className="w-4 h-4 text-gray-400 rtl:rotate-180" aria-hidden="true" />
                <li className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5" data-testid="trace-work-batches">
                  <span className="text-xs text-gray-500 block">{ts('bt_trace_work_batches', language)}</span>
                  {data.work_batches.length === 0
                    ? <span className="text-gray-500">{ts('bt_trace_none_work', language)}</span>
                    : data.work_batches.map((b) => <span key={b.id} className="me-2">{link(fill(ts('bt_work_n', language), { n: b.seq }), () => onOpenWork(b.id))}</span>)}
                </li>
              </ol>
              <section>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('bt_trace_attempts', language)}</h3>
                <AttemptsList attempts={data.attempts} language={language} />
              </section>
              <section>
                <h3 className="text-sm font-semibold text-[#0A0A0A] mb-2">{ts('bt_trace_timeline', language)}</h3>
                <LeadTimeline leadId={data.id} language={language} sources={ref.sources} />
              </section>
            </div>
          )}
        </Body>
      </DialogContent>
    </Dialog>
  );
};
