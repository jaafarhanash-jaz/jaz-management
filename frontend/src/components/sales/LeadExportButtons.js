import { useState } from 'react';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { apiErrorMessage } from '@/utils/salesErrors';
import { ts } from '@/utils/salesTranslations';
import { FileSpreadsheet, FileText } from 'lucide-react';
import { toast } from 'sonner';

// Export the leads the Leads page shows - ALL of them that match the current search / filters / sort, not the current page -
// as Excel or PDF (GET /sales/leads/export, sales.leads.export). The file is built by the server in the page's language
// (Arabic RTL or English); the browser only saves it.
const stamp = () => {
  const d = new Date();
  const two = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}`;
};

// A failed blob request carries its JSON error as a Blob: turn it back into the shape the error helpers read.
const readBlobError = async (err) => {
  const data = err?.response?.data;
  if (data instanceof Blob) {
    try {
      return { response: { status: err.response.status, data: JSON.parse(await data.text()) } };
    } catch (e) {
      return err;
    }
  }
  return err;
};

const LeadExportButtons = ({ query, language }) => {
  const [busy, setBusy] = useState(null);             // 'xlsx' | 'pdf' | null

  const run = async (format) => {
    setBusy(format);
    try {
      const res = await api.get('/sales/leads/export', { params: { ...query, format, lang: language === 'ar' ? 'ar' : 'en' }, responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `jaz-leads-${stamp()}.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const count = res.headers?.['x-export-row-count'];
      toast.success(count !== undefined ? ts('exp_done', language).replace('{n}', count) : ts('exp_done_generic', language));
    } catch (err) {
      toast.error(apiErrorMessage(await readBlobError(err), language));
    }
    setBusy(null);
  };

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={ts('exp_hint', language)} title={ts('exp_hint', language)} data-testid="lead-export">
      <Button type="button" variant="outline" className="rounded-sm" disabled={busy !== null} onClick={() => run('xlsx')} data-testid="export-xlsx">
        <FileSpreadsheet className="w-4 h-4 me-2" aria-hidden="true" />{busy === 'xlsx' ? ts('exp_working', language) : ts('exp_excel', language)}
      </Button>
      <Button type="button" variant="outline" className="rounded-sm" disabled={busy !== null} onClick={() => run('pdf')} data-testid="export-pdf">
        <FileText className="w-4 h-4 me-2" aria-hidden="true" />{busy === 'pdf' ? ts('exp_working', language) : ts('exp_pdf', language)}
      </Button>
    </div>
  );
};

export default LeadExportButtons;
