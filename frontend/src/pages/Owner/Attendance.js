import { useEffect, useState, useRef, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { MapContainer, TileLayer, Marker, Circle, useMapEvents, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';
import { jsPDF } from 'jspdf';
import html2canvas from 'html2canvas';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import {
  Clock, CheckCircle, MapPin, QrCode, Download, Printer,
  AlertTriangle, Pencil, LogOut, Percent, Users, LocateFixed, FileText,
} from 'lucide-react';
import { toast } from 'sonner';

// Webpack doesn't resolve leaflet's default marker asset URLs on its own.
L.Marker.prototype.options.icon = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

const todayStr = () => new Date().toISOString().split('T')[0];
const daysAgoStr = (n) => new Date(Date.now() - n * 86400000).toISOString().split('T')[0];

const formatTime = (iso) => {
  if (!iso) return '-';
  return new Date(iso).toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' });
};

const formatDuration = (minutes) => {
  if (minutes === null || minutes === undefined) return '-';
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `${h}س ${m}د`;
};

const statusBadge = (status) => (
  <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${
    status === 'present' ? 'bg-green-50 text-green-700 border-green-200' :
    status === 'late' ? 'bg-yellow-50 text-yellow-700 border-yellow-200' :
    'bg-red-50 text-red-700 border-red-200'
  }`}>
    {status === 'present' ? 'حاضر' : status === 'late' ? 'متأخر' : 'غائب'}
  </span>
);

// Company Holiday Management - a record on a non-working date shows this
// instead of the normal status badge, never Absent/Late/Missing, per the
// explicit "Reports must show Official Company Holiday or Weekly Holiday"
// requirement.
const holidayBadge = (holidayType, title) => (
  <span className="px-2.5 py-0.5 rounded-full text-xs font-medium border bg-emerald-50 text-emerald-700 border-emerald-200" title={title || undefined}>
    {holidayType === 'weekly_holiday' ? 'عطلة أسبوعية' : 'عطلة رسمية للشركة'}
  </span>
);

const openMap = (loc) => {
  if (loc && loc.latitude && loc.longitude) {
    window.open(`https://www.openstreetmap.org/?mlat=${loc.latitude}&mlon=${loc.longitude}#map=17/${loc.latitude}/${loc.longitude}`, '_blank');
  }
};

const buildQuery = (f) => {
  const params = new URLSearchParams();
  if (f.date_from) params.set('date_from', f.date_from);
  if (f.date_to) params.set('date_to', f.date_to);
  if (f.employee_id) params.set('employee_id', f.employee_id);
  if (f.department) params.set('department', f.department);
  if (f.status) params.set('status', f.status);
  if (f.search) params.set('search', f.search);
  return params.toString();
};

const isoToLocalInput = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const selectClass = 'h-9 rounded-md border border-input bg-white px-3 text-sm';

function MapClickHandler({ onPick }) {
  useMapEvents({ click(e) { onPick([e.latlng.lat, e.latlng.lng]); } });
  return null;
}

function MapRecenter({ position }) {
  const map = useMap();
  useEffect(() => { if (position) map.flyTo(position, 16); }, [position, map]);
  return null;
}

const OwnerAttendance = ({ onLogout, language, setLanguage }) => {
  const [activeTab, setActiveTab] = useState('records');
  const [employees, setEmployees] = useState([]);

  // Records tab
  const [records, setRecords] = useState([]);
  const [recordsLoading, setRecordsLoading] = useState(true);
  const [stats, setStats] = useState(null);
  const [filters, setFilters] = useState({
    search: '', employee_id: '', department: '', status: '',
    date_from: todayStr(), date_to: todayStr(),
  });
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ check_in_time: '', check_out_time: '', status: '' });

  // QR + Settings tabs (one shared fetch)
  const [qrData, setQrData] = useState(null);
  const [settingsForm, setSettingsForm] = useState(null);
  const [savingSettings, setSavingSettings] = useState(false);
  // Location starts locked (View Mode) once a location is already saved;
  // Setup Mode (no location yet) and pressing "Change Location" are the only
  // ways to make the map interactive - see isMapEditable below.
  const [locationEditing, setLocationEditing] = useState(false);

  // Analytics tab
  const [analytics, setAnalytics] = useState(null);

  // Audit log (Records tab)
  const [auditLogOpen, setAuditLogOpen] = useState(false);
  const [auditLog, setAuditLog] = useState(null);

  // Reports tab
  const [report, setReport] = useState([]);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportFilters, setReportFilters] = useState({
    search: '', employee_id: '', department: '', status: '',
    date_from: todayStr(), date_to: todayStr(),
  });
  const reportTableRef = useRef(null);

  const departments = [...new Set(employees.map((e) => e.department).filter(Boolean))];

  useEffect(() => {
    api.get('/owner/employees').then((res) => setEmployees(res.data)).catch(() => {});
  }, []);

  // Live summary tiles: same silent 10s polling pattern as the Owner Dashboard.
  useEffect(() => {
    const fetchStats = () => api.get('/owner/dashboard').then((res) => setStats(res.data)).catch(() => {});
    fetchStats();
    const interval = setInterval(fetchStats, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchRecords = useCallback(async (silent = false) => {
    if (!silent) setRecordsLoading(true);
    try {
      const res = await api.get(`/owner/attendance?${buildQuery(filters)}`);
      setRecords(res.data);
    } catch (e) {
      if (!silent) toast.error('خطأ في جلب البيانات');
    }
    if (!silent) setRecordsLoading(false);
  }, [filters]);

  useEffect(() => {
    fetchRecords();
    const interval = setInterval(() => fetchRecords(true), 10000);
    return () => clearInterval(interval);
  }, [fetchRecords]);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await api.get('/owner/attendance/settings');
      setQrData(res.data);
      setSettingsForm({
        position: res.data.settings.latitude !== null && res.data.settings.longitude !== null
          ? [res.data.settings.latitude, res.data.settings.longitude]
          : null,
        radius: res.data.settings.radius_meters,
        qrEnabled: res.data.settings.qr_enabled,
        updatedAt: res.data.settings.updated_at,
        updatedByName: res.data.settings.updated_by_name,
      });
    } catch (e) {
      toast.error('خطأ في جلب إعدادات الحضور');
    }
  }, []);

  useEffect(() => {
    if ((activeTab === 'qr' || activeTab === 'settings') && !qrData) fetchSettings();
    if (activeTab === 'analytics' && !analytics) {
      api.get('/owner/attendance/analytics').then((res) => setAnalytics(res.data)).catch(() => toast.error('خطأ في جلب التحليلات'));
    }
  }, [activeTab, qrData, analytics, fetchSettings]);

  useEffect(() => {
    if (activeTab !== 'reports') return;
    let cancelled = false;
    setReportLoading(true);
    api.get(`/owner/attendance?${buildQuery(reportFilters)}`)
      .then((res) => { if (!cancelled) setReport(res.data); })
      .catch(() => { if (!cancelled) toast.error('خطأ في جلب التقرير'); })
      .finally(() => { if (!cancelled) setReportLoading(false); });
    return () => { cancelled = true; };
  }, [activeTab, reportFilters]);

  // ---- Records: manual correction (audit-logged server-side) ----
  const openEdit = (rec) => {
    setEditing(rec);
    setEditForm({
      check_in_time: isoToLocalInput(rec.check_in_time),
      check_out_time: isoToLocalInput(rec.check_out_time),
      status: rec.status,
    });
  };

  const saveEdit = async () => {
    const payload = {};
    const newCheckIn = editForm.check_in_time ? new Date(editForm.check_in_time).toISOString() : null;
    const newCheckOut = editForm.check_out_time ? new Date(editForm.check_out_time).toISOString() : null;
    if (newCheckIn && newCheckIn !== editing.check_in_time) payload.check_in_time = newCheckIn;
    if (newCheckOut && newCheckOut !== editing.check_out_time) payload.check_out_time = newCheckOut;
    if (editForm.status && editForm.status !== editing.status) payload.status = editForm.status;
    if (Object.keys(payload).length === 0) { setEditing(null); return; }
    try {
      await api.patch(`/owner/attendance/${editing.id}`, payload);
      toast.success('تم تعديل السجل وتوثيق التغيير');
      setEditing(null);
      fetchRecords(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  // ---- QR actions ----
  const downloadQrPng = () => {
    const link = document.createElement('a');
    link.href = qrData.qr_code;
    link.download = 'jaz-attendance-qr.png';
    link.click();
  };

  const downloadQrPdf = () => {
    const doc = new jsPDF();
    doc.setFontSize(22);
    doc.text('JAZ - Attendance QR Code', 105, 30, { align: 'center' });
    doc.addImage(qrData.qr_code, 'PNG', 55, 50, 100, 100);
    doc.setFontSize(11);
    doc.text('Scan this code to check in / check out', 105, 165, { align: 'center' });
    doc.save('jaz-attendance-qr.pdf');
  };

  const printQr = () => {
    const win = window.open('', '_blank');
    win.document.write(`
      <html dir="rtl"><head><title>رمز الحضور</title></head>
      <body style="display:flex;flex-direction:column;align-items:center;font-family:sans-serif;padding-top:40px">
        <h1>رمز تسجيل الحضور</h1>
        <img src="${qrData.qr_code}" style="width:400px;height:400px" />
        <p>امسح هذا الرمز لتسجيل الحضور والانصراف</p>
        <script>window.onload = () => window.print();</script>
      </body></html>`);
    win.document.close();
  };

  // ---- Settings actions ----
  const useCurrentLocation = () => {
    if (!navigator.geolocation) { toast.error('المتصفح لا يدعم تحديد الموقع'); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => setSettingsForm((f) => ({ ...f, position: [pos.coords.latitude, pos.coords.longitude] })),
      () => toast.error('تعذر الحصول على موقعك الحالي. تأكد من السماح بالوصول إلى الموقع.'),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      const payload = {
        radius_meters: Number(settingsForm.radius),
        qr_enabled: settingsForm.qrEnabled,
      };
      if (settingsForm.position) {
        payload.latitude = settingsForm.position[0];
        payload.longitude = settingsForm.position[1];
      }
      const res = await api.put('/owner/attendance/settings', payload);
      setQrData((prev) => ({ ...prev, settings: res.data.settings, location_configured: res.data.location_configured }));
      setSettingsForm((f) => ({ ...f, updatedAt: res.data.settings.updated_at, updatedByName: res.data.settings.updated_by_name }));
      // A successful save always returns to View Mode, whether this was the
      // first-ever Setup or a location change - Settings never stays "open
      // for editing" once persisted.
      setLocationEditing(false);
      toast.success('تم حفظ إعدادات الحضور');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
    setSavingSettings(false);
  };

  // ---- Audit log (read-only) ----
  const openAuditLog = async () => {
    setAuditLogOpen(true);
    if (auditLog) return;
    try {
      const res = await api.get('/owner/attendance/audit-log');
      setAuditLog(res.data);
    } catch (e) {
      toast.error('خطأ في جلب سجل التعديلات');
    }
  };

  const AUDIT_FIELD_LABELS = { check_in_time: 'وقت الدخول', check_out_time: 'وقت الخروج', status: 'الحالة' };
  const formatAuditValue = (field, value) => {
    if (value === null || value === undefined) return '-';
    if (field === 'check_in_time' || field === 'check_out_time') return formatTime(value);
    if (field === 'status') return value === 'present' ? 'حاضر' : value === 'late' ? 'متأخر' : 'غائب';
    return String(value);
  };

  // ---- Report exports ----
  const reportRows = () => [
    ['التاريخ', 'الموظف', 'القسم', 'الدخول', 'الخروج', 'مدة العمل', 'الحالة', 'المسافة (متر)'],
    ...report.map((r) => [
      r.date, r.employee_name || '-', r.employee_department || '-',
      formatTime(r.check_in_time), formatTime(r.check_out_time),
      formatDuration(r.working_duration_minutes),
      r.is_holiday ? (r.holiday_type === 'weekly_holiday' ? 'عطلة أسبوعية' : 'عطلة رسمية للشركة') : (r.status === 'present' ? 'حاضر' : r.status === 'late' ? 'متأخر' : 'غائب'),
      r.distance_from_company_meters ?? '-',
    ]),
  ];

  const exportExcel = () => {
    // Same BOM+CSV pattern as Owner/Reports.js - opens directly in Excel.
    const csv = '\uFEFF' + reportRows().map((row) => row.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `attendance_${reportFilters.date_from}_${reportFilters.date_to}.csv`;
    link.click();
  };

  const exportPdf = async () => {
    if (!reportTableRef.current) return;
    // Rasterizing the rendered table keeps Arabic text intact - jsPDF has no
    // native Arabic shaping.
    const canvas = await html2canvas(reportTableRef.current, { scale: 2 });
    const img = canvas.toDataURL('image/png');
    const pdf = new jsPDF({ orientation: 'p', unit: 'mm', format: 'a4' });
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    const imgWidth = pageWidth - 20;
    const imgHeight = (canvas.height * imgWidth) / canvas.width;
    let heightLeft = imgHeight;
    let position = 10;
    pdf.addImage(img, 'PNG', 10, position, imgWidth, imgHeight);
    heightLeft -= pageHeight - 20;
    while (heightLeft > 0) {
      pdf.addPage();
      position = heightLeft - imgHeight + 10;
      pdf.addImage(img, 'PNG', 10, position, imgWidth, imgHeight);
      heightLeft -= pageHeight - 20;
    }
    pdf.save(`attendance_${reportFilters.date_from}_${reportFilters.date_to}.pdf`);
  };

  const printReport = () => {
    const rows = reportRows();
    const head = rows[0].map((h) => `<th>${h}</th>`).join('');
    const body = rows.slice(1).map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('');
    const win = window.open('', '_blank');
    win.document.write(`
      <html dir="rtl"><head><title>تقرير الحضور</title>
      <style>
        body { font-family: sans-serif; padding: 24px; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; }
        th { background: #f4f4f5; }
      </style></head>
      <body>
        <h2>تقرير الحضور (${reportFilters.date_from} - ${reportFilters.date_to})</h2>
        <table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
        <script>window.onload = () => window.print();</script>
      </body></html>`);
    win.document.close();
  };

  const presets = [
    { label: 'اليوم', from: todayStr(), to: todayStr() },
    { label: 'أمس', from: daysAgoStr(1), to: daysAgoStr(1) },
    { label: 'هذا الأسبوع', from: daysAgoStr(6), to: todayStr() },
    { label: 'هذا الشهر', from: `${todayStr().slice(0, 8)}01`, to: todayStr() },
  ];

  const filterControls = (state, setState) => (
    <div className="flex flex-wrap items-end gap-3">
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">بحث</label>
        <Input data-testid="attendance-search-input" value={state.search} placeholder="اسم الموظف..."
          onChange={(e) => setState({ ...state, search: e.target.value })} className="w-44" />
      </div>
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الموظف</label>
        <select data-testid="attendance-employee-filter" className={selectClass} value={state.employee_id}
          onChange={(e) => setState({ ...state, employee_id: e.target.value })}>
          <option value="">الكل</option>
          {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
      </div>
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">القسم</label>
        <select data-testid="attendance-department-filter" className={selectClass} value={state.department}
          onChange={(e) => setState({ ...state, department: e.target.value })}>
          <option value="">الكل</option>
          {departments.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الحالة</label>
        <select data-testid="attendance-status-filter" className={selectClass} value={state.status}
          onChange={(e) => setState({ ...state, status: e.target.value })}>
          <option value="">الكل</option>
          <option value="present">حاضر</option>
          <option value="late">متأخر</option>
          <option value="checked_out">انصرف</option>
        </select>
      </div>
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">من</label>
        <Input data-testid="attendance-date-from" type="date" value={state.date_from}
          onChange={(e) => setState({ ...state, date_from: e.target.value })} className="w-40" />
      </div>
      <div>
        <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">إلى</label>
        <Input data-testid="attendance-date-to" type="date" value={state.date_to}
          onChange={(e) => setState({ ...state, date_to: e.target.value })} className="w-40" />
      </div>
    </div>
  );

  const recordsTable = (rows, { editable = false, tableRef = null } = {}) => (
    <Card className="bg-white border border-gray-200 rounded-md shadow-sm">
      <div className="overflow-x-auto" ref={tableRef}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50/50">
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">التاريخ</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الموظف</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">القسم</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الدخول</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الخروج</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">مدة العمل</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الحالة</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">المسافة</th>
              <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">الموقع</th>
              {editable && <th className="text-start px-4 py-3 text-xs font-medium text-gray-500 uppercase">تعديل</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((rec) => (
              <tr key={rec.id} className="border-b border-gray-100 hover:bg-gray-50/50" data-testid={`attendance-row-${rec.id}`}>
                <td className="px-4 py-3 font-medium">{rec.date}</td>
                <td className="px-4 py-3 font-medium text-[#0A0A0A]">{rec.employee_name}</td>
                <td className="px-4 py-3 text-gray-600">{rec.employee_department || '-'}</td>
                <td className="px-4 py-3 text-gray-600">{formatTime(rec.check_in_time)}</td>
                <td className="px-4 py-3 text-gray-600">{formatTime(rec.check_out_time)}</td>
                <td className="px-4 py-3 text-gray-600">{formatDuration(rec.working_duration_minutes)}</td>
                <td className="px-4 py-3">{rec.is_holiday ? holidayBadge(rec.holiday_type, rec.holiday_title) : statusBadge(rec.status)}</td>
                <td className="px-4 py-3 text-gray-600 text-xs">
                  {rec.distance_from_company_meters !== null && rec.distance_from_company_meters !== undefined
                    ? `${Math.round(rec.distance_from_company_meters)} م` : '-'}
                </td>
                <td className="px-4 py-3">
                  {rec.check_in_location ? (
                    <button onClick={() => openMap(rec.check_in_location)} className="text-[#0033A0] hover:underline flex items-center gap-1 text-xs" data-testid={`map-btn-${rec.id}`}>
                      <MapPin className="w-3 h-3" /> عرض
                    </button>
                  ) : <span className="text-gray-400 text-xs">-</span>}
                </td>
                {editable && (
                  <td className="px-4 py-3">
                    <button onClick={() => openEdit(rec)} className="text-gray-500 hover:text-[#0033A0]" data-testid={`edit-btn-${rec.id}`}>
                      <Pencil className="w-4 h-4" />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );

  const liveTiles = stats ? [
    { label: 'الحاضرون', value: stats.present_today, icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50' },
    { label: 'الغائبون', value: stats.absent_today, icon: AlertTriangle, color: 'text-red-600', bg: 'bg-red-50' },
    { label: 'المتأخرون', value: stats.late_today, icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50' },
    { label: 'انصرفوا', value: stats.checked_out_today ?? 0, icon: LogOut, color: 'text-slate-600', bg: 'bg-slate-50' },
    { label: 'نسبة الحضور', value: `${stats.attendance_percentage ?? 0}%`, icon: Percent, color: 'text-emerald-600', bg: 'bg-emerald-50' },
  ] : [];

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="attendance-title">
            {t('attendance', language)}
          </h1>
          <p className="text-gray-600 mt-2">إدارة الحضور والانصراف عبر QR</p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} dir={language === 'ar' ? 'rtl' : 'ltr'}>
          <TabsList className="flex-wrap h-auto">
            <TabsTrigger value="records" data-testid="tab-records">سجلات الحضور</TabsTrigger>
            <TabsTrigger value="qr" data-testid="tab-qr">رمز QR</TabsTrigger>
            <TabsTrigger value="settings" data-testid="tab-settings">إعدادات الحضور</TabsTrigger>
            <TabsTrigger value="analytics" data-testid="tab-analytics">التحليلات</TabsTrigger>
            <TabsTrigger value="reports" data-testid="tab-reports">التقارير</TabsTrigger>
          </TabsList>

          {/* ============ 1. Records ============ */}
          <TabsContent value="records" className="space-y-6 mt-6">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {liveTiles.map((tile, i) => {
                const Icon = tile.icon;
                return (
                  <Card key={i} className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`live-tile-${i}`}>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-xs text-gray-500 mb-1">{tile.label}</p>
                        <p className="text-2xl font-bold text-[#0A0A0A]">{tile.value}</p>
                      </div>
                      <div className={`p-2 rounded-sm ${tile.bg}`}>
                        <Icon className={`w-5 h-5 ${tile.color}`} />
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>

            <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-3">
              {filterControls(filters, setFilters)}
              <div className="flex justify-end border-t border-gray-100 pt-3">
                <Button onClick={openAuditLog} variant="outline" size="sm" className="rounded-sm text-xs" data-testid="open-audit-log-btn">
                  <FileText className="w-3.5 h-3.5 me-2" /> سجل التعديلات
                </Button>
              </div>
            </Card>

            {recordsLoading ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : records.length === 0 ? (
              <Card className="p-12 text-center bg-white border border-gray-200">
                <Clock className="w-12 h-12 mx-auto text-gray-300 mb-4" />
                <p className="text-gray-500">لا توجد سجلات حضور مطابقة</p>
              </Card>
            ) : recordsTable(records, { editable: true })}
          </TabsContent>

          {/* ============ 2. QR Code ============ */}
          <TabsContent value="qr" className="mt-6">
            {!qrData ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : (
              <Card className="p-8 bg-white border border-gray-200 rounded-md max-w-xl mx-auto text-center space-y-6">
                <div className="flex items-center justify-center gap-2">
                  <QrCode className="w-6 h-6 text-[#0033A0]" />
                  <h2 className="text-xl font-bold text-[#0A0A0A]">رمز الحضور الخاص بشركتك</h2>
                </div>
                <p className="text-sm text-gray-600">
                  اطبع هذا الرمز وضعه داخل الشركة. يقوم الموظفون بمسحه لتسجيل الحضور والانصراف.
                  الرمز لا يحتوي على أي معلومات عن الشركة، وهو ثابت بشكل دائم ولا يتغير.
                </p>
                <img src={qrData.qr_code} alt="Company QR" className="w-64 h-64 mx-auto border border-gray-200 rounded-md" data-testid="company-qr-img" />

                <div className="text-start bg-gray-50 border border-gray-200 rounded-md p-4 space-y-2" data-testid="qr-info-block">
                  <div className="flex justify-between text-sm"><span className="text-gray-500">اسم الشركة</span><span className="font-medium text-[#0A0A0A]">{qrData.company_name}</span></div>
                  <div className="flex justify-between text-sm"><span className="text-gray-500">خط العرض</span><span className="font-medium text-[#0A0A0A]">{qrData.settings.latitude !== null ? qrData.settings.latitude.toFixed(6) : 'غير محدد'}</span></div>
                  <div className="flex justify-between text-sm"><span className="text-gray-500">خط الطول</span><span className="font-medium text-[#0A0A0A]">{qrData.settings.longitude !== null ? qrData.settings.longitude.toFixed(6) : 'غير محدد'}</span></div>
                  <div className="flex justify-between text-sm"><span className="text-gray-500">نطاق الحضور</span><span className="font-medium text-[#0A0A0A]">{qrData.settings.radius_meters} متر</span></div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-500">حالة رمز QR</span>
                    <span className={`font-medium ${qrData.settings.qr_enabled ? 'text-green-600' : 'text-red-600'}`}>
                      {qrData.settings.qr_enabled ? 'نشط' : 'معطل'}
                    </span>
                  </div>
                </div>

                <div className="flex flex-wrap justify-center gap-3">
                  <Button onClick={downloadQrPng} variant="outline" className="rounded-sm" data-testid="qr-download-png">
                    <Download className="w-4 h-4 me-2" /> تحميل PNG
                  </Button>
                  <Button onClick={downloadQrPdf} variant="outline" className="rounded-sm" data-testid="qr-download-pdf">
                    <FileText className="w-4 h-4 me-2" /> تحميل PDF
                  </Button>
                  <Button onClick={printQr} variant="outline" className="rounded-sm" data-testid="qr-print">
                    <Printer className="w-4 h-4 me-2" /> طباعة
                  </Button>
                </div>
              </Card>
            )}
          </TabsContent>

          {/* ============ 3. Settings ============ */}
          <TabsContent value="settings" className="mt-6">
            {!settingsForm ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : (() => {
              // Setup Mode: no location saved yet - the map opens interactive
              // automatically. Otherwise the page always opens in View Mode
              // (read-only map) until the Owner explicitly presses "Change
              // Location". Radius/QR-enabled are independent of this and stay
              // editable regardless of mode.
              const isSetupMode = !qrData?.location_configured;
              const isMapEditable = isSetupMode || locationEditing;
              return (
              <div className="max-w-3xl space-y-6">
                {isSetupMode && (
                  <Card className="p-4 bg-amber-50 border border-amber-200 rounded-md flex items-start gap-3" data-testid="location-warning">
                    <AlertTriangle className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
                    <p className="text-sm text-amber-800">
                      لم يتم تحديد موقع الشركة بعد. لن يتم التحقق من نطاق الموقع عند تسجيل الحضور
                      حتى تقوم بتحديد الموقع أدناه وحفظه. هذا الإعداد يتم مرة واحدة فقط.
                    </p>
                  </Card>
                )}

                <Card className="p-6 bg-white border border-gray-200 rounded-md space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-bold text-[#0A0A0A]">تفعيل الحضور عبر QR</h3>
                      <p className="text-xs text-gray-500 mt-1">عند الإيقاف لن يتمكن الموظفون من تسجيل الحضور</p>
                    </div>
                    <Switch checked={settingsForm.qrEnabled} data-testid="qr-enabled-switch"
                      onCheckedChange={(v) => setSettingsForm({ ...settingsForm, qrEnabled: v })} />
                  </div>
                </Card>

                <Card className="p-6 bg-white border border-gray-200 rounded-md space-y-4">
                  <div className="flex items-center justify-between flex-wrap gap-3">
                    <div>
                      <h3 className="font-bold text-[#0A0A0A]">موقع الشركة</h3>
                      <p className="text-xs text-gray-500 mt-1">
                        {isMapEditable ? 'اضغط على الخريطة لتحديد الموقع أو استخدم موقعك الحالي' : 'الموقع محفوظ ولن يتغير إلا بضغط "تغيير الموقع"'}
                      </p>
                    </div>
                    {isMapEditable ? (
                      <Button onClick={useCurrentLocation} variant="outline" className="rounded-sm" data-testid="use-current-location">
                        <LocateFixed className="w-4 h-4 me-2" /> استخدام موقعي الحالي
                      </Button>
                    ) : (
                      <Button onClick={() => setLocationEditing(true)} variant="outline" className="rounded-sm" data-testid="change-location-btn">
                        <MapPin className="w-4 h-4 me-2" /> تغيير الموقع
                      </Button>
                    )}
                  </div>

                  {/* pointer-events-none is what actually makes the map
                      read-only (blocks pan/zoom/click at the DOM level) and,
                      unlike react-leaflet's dragging/scrollWheelZoom props,
                      reacts correctly every time isMapEditable flips - those
                      props are only applied once at map construction. The
                      conditional MapClickHandler below is a second layer that
                      also stops the pin itself from moving. */}
                  <MapContainer
                    center={settingsForm.position || [24.7136, 46.6753]}
                    zoom={settingsForm.position ? 16 : 10}
                    style={{ height: 320, width: '100%' }}
                    className={`rounded-md z-0 ${isMapEditable ? '' : 'pointer-events-none opacity-90'}`}
                  >
                    <TileLayer
                      url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                    />
                    {isMapEditable && <MapClickHandler onPick={(pos) => setSettingsForm((f) => ({ ...f, position: pos }))} />}
                    <MapRecenter position={settingsForm.position} />
                    {settingsForm.position && <Marker position={settingsForm.position} />}
                    {settingsForm.position && Number(settingsForm.radius) > 0 && (
                      <Circle center={settingsForm.position} radius={Number(settingsForm.radius)} pathOptions={{ color: '#0033A0' }} />
                    )}
                  </MapContainer>

                  {settingsForm.position && (
                    <p className="text-xs text-gray-500" data-testid="picked-coords">
                      خط العرض: {settingsForm.position[0].toFixed(6)} · خط الطول: {settingsForm.position[1].toFixed(6)}
                    </p>
                  )}

                  {!isSetupMode && (
                    <div className="grid grid-cols-2 gap-3 pt-2 border-t border-gray-100 text-xs" data-testid="settings-meta">
                      <div>
                        <p className="text-gray-500">آخر تحديث</p>
                        <p className="font-medium text-[#0A0A0A]">{settingsForm.updatedAt ? new Date(settingsForm.updatedAt).toLocaleString('ar-EG') : '-'}</p>
                      </div>
                      <div>
                        <p className="text-gray-500">تم التحديث بواسطة</p>
                        <p className="font-medium text-[#0A0A0A]">{settingsForm.updatedByName || '-'}</p>
                      </div>
                    </div>
                  )}

                  <div className="flex items-end gap-4 flex-wrap pt-2">
                    <div>
                      <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">النطاق المسموح (متر)</label>
                      <Input type="number" min="1" value={settingsForm.radius} data-testid="radius-input"
                        onChange={(e) => setSettingsForm({ ...settingsForm, radius: e.target.value })} className="w-36" />
                      <p className="text-[11px] text-gray-400 mt-1">يمكن تغيير النطاق دون الحاجة لتغيير الموقع</p>
                    </div>
                    <Button onClick={saveSettings} disabled={savingSettings || (isMapEditable && !settingsForm.position)} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="save-settings-btn">
                      {savingSettings ? 'جارٍ الحفظ...' : 'حفظ الإعدادات'}
                    </Button>
                  </div>
                </Card>
              </div>
              );
            })()}
          </TabsContent>

          {/* ============ 4. Analytics ============ */}
          <TabsContent value="analytics" className="space-y-6 mt-6">
            {!analytics ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : (
              <>
                <p className="text-xs text-gray-500">الفترة: {analytics.date_from} إلى {analytics.date_to} (آخر 30 يوماً)</p>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                  {[
                    { label: 'متوسط وقت الدخول', value: analytics.average_check_in_time ?? '-' },
                    { label: 'متوسط وقت الخروج', value: analytics.average_check_out_time ?? '-' },
                    { label: 'متوسط ساعات العمل', value: analytics.average_working_hours },
                    { label: 'نسبة الحضور', value: `${analytics.attendance_percentage}%` },
                    { label: 'عدد التأخيرات', value: analytics.late_count },
                    { label: 'عدد الغيابات', value: analytics.absence_count },
                    { label: 'نسبة التأخير', value: `${analytics.late_percentage}%` },
                    { label: 'متوسط المسافة عن الشركة', value: analytics.average_distance_meters !== null ? `${analytics.average_distance_meters} م` : '-' },
                  ].map((stat, i) => (
                    <Card key={i} className="p-4 bg-white border border-gray-200 rounded-md" data-testid={`att-analytics-stat-${i}`}>
                      <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">{stat.label}</p>
                      <p className="text-2xl font-bold text-[#0A0A0A]">{stat.value}</p>
                    </Card>
                  ))}
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">اتجاه الحضور</h3>
                    <ResponsiveContainer width="100%" height={240}>
                      <LineChart data={analytics.charts.attendance_trend}>
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                        <YAxis domain={[0, 100]} />
                        <Tooltip />
                        <Line type="monotone" dataKey="rate" stroke="#00A36C" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Card>
                  <Card className="p-6 bg-white border border-gray-200 rounded-md">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">اتجاه التأخير</h3>
                    <ResponsiveContainer width="100%" height={240}>
                      <LineChart data={analytics.charts.late_trend}>
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                        <YAxis allowDecimals={false} />
                        <Tooltip />
                        <Line type="monotone" dataKey="count" stroke="#FFB800" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Card>
                  <Card className="p-6 bg-white border border-gray-200 rounded-md lg:col-span-2">
                    <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">اتجاه ساعات العمل</h3>
                    <ResponsiveContainer width="100%" height={240}>
                      <LineChart data={analytics.charts.working_hours_trend}>
                        <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                        <YAxis />
                        <Tooltip />
                        <Line type="monotone" dataKey="hours" stroke="#0033A0" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Card>
                </div>

                <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="department-attendance">
                  <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">نسبة الحضور حسب القسم</h3>
                  {analytics.department_attendance.length > 0 ? (
                    <ResponsiveContainer width="100%" height={240}>
                      <BarChart data={analytics.department_attendance}>
                        <XAxis dataKey="department" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 100]} />
                        <Tooltip />
                        <Bar dataKey="attendance_rate" fill="#0033A0" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : <p className="text-center text-gray-500 py-12">لا توجد بيانات أقسام</p>}
                </Card>

                <Card className="p-6 bg-white border border-gray-200 rounded-md" data-testid="attendance-ranking">
                  <div className="flex items-center gap-2 mb-4">
                    <Users className="w-5 h-5 text-[#0033A0]" />
                    <h3 className="text-lg font-medium text-[#0A0A0A]">ترتيب التزام الموظفين</h3>
                  </div>
                  <div className="space-y-2">
                    {analytics.employee_ranking.map((e) => (
                      <div key={e.employee_id} className="flex items-center justify-between p-3 border border-gray-100 rounded-sm" data-testid={`att-ranking-${e.employee_id}`}>
                        <div className="flex items-center gap-3">
                          <span className="w-6 h-6 flex items-center justify-center bg-[#0033A0] text-white text-xs font-bold rounded-full">{e.rank}</span>
                          <div>
                            <p className="font-medium text-[#0A0A0A]">{e.name}</p>
                            <p className="text-xs text-gray-500">
                              حضور {e.days_attended} يوم · تأخير {e.late_count} · متوسط {e.avg_working_hours} ساعة
                            </p>
                          </div>
                        </div>
                        <p className="text-xl font-bold text-[#0033A0]">{e.score}%</p>
                      </div>
                    ))}
                    {analytics.employee_ranking.length === 0 && <p className="text-center text-gray-500 py-6">لا يوجد موظفون</p>}
                  </div>
                </Card>
              </>
            )}
          </TabsContent>

          {/* ============ 5. Reports ============ */}
          <TabsContent value="reports" className="space-y-6 mt-6">
            <Card className="p-4 bg-white border border-gray-200 rounded-md space-y-4">
              <div className="flex flex-wrap gap-2">
                {presets.map((p) => (
                  <Button key={p.label} variant="outline" size="sm" className="rounded-sm text-xs"
                    data-testid={`preset-${p.label}`}
                    onClick={() => setReportFilters({ ...reportFilters, date_from: p.from, date_to: p.to })}>
                    {p.label}
                  </Button>
                ))}
              </div>
              {filterControls(reportFilters, setReportFilters)}
              <div className="flex flex-wrap gap-3 pt-2 border-t border-gray-100">
                <Button onClick={exportPdf} variant="outline" className="rounded-sm" disabled={report.length === 0} data-testid="export-pdf-btn">
                  <FileText className="w-4 h-4 me-2" /> تصدير PDF
                </Button>
                <Button onClick={exportExcel} variant="outline" className="rounded-sm" disabled={report.length === 0} data-testid="export-excel-btn">
                  <Download className="w-4 h-4 me-2" /> تصدير Excel
                </Button>
                <Button onClick={printReport} variant="outline" className="rounded-sm" disabled={report.length === 0} data-testid="print-report-btn">
                  <Printer className="w-4 h-4 me-2" /> طباعة
                </Button>
              </div>
            </Card>

            {reportLoading ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : report.length === 0 ? (
              <Card className="p-12 text-center bg-white border border-gray-200">
                <Clock className="w-12 h-12 mx-auto text-gray-300 mb-4" />
                <p className="text-gray-500">لا توجد سجلات في هذه الفترة</p>
              </Card>
            ) : recordsTable(report, { tableRef: reportTableRef })}
          </TabsContent>
        </Tabs>

        {/* Manual correction dialog - every change is audit-logged server-side */}
        <Dialog open={!!editing} onOpenChange={(open) => { if (!open) setEditing(null); }}>
          <DialogContent className="max-w-md" data-testid="edit-attendance-dialog">
            <DialogHeader>
              <DialogTitle>تعديل سجل حضور - {editing?.employee_name}</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-sm p-2">
                سيتم توثيق هذا التعديل في سجل المراجعة (من قام بالتعديل، متى، القيمة القديمة والجديدة).
              </p>
              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">وقت الدخول</label>
                <Input type="datetime-local" value={editForm.check_in_time} data-testid="edit-checkin-input"
                  onChange={(e) => setEditForm({ ...editForm, check_in_time: e.target.value })} />
              </div>
              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">وقت الخروج</label>
                <Input type="datetime-local" value={editForm.check_out_time} data-testid="edit-checkout-input"
                  onChange={(e) => setEditForm({ ...editForm, check_out_time: e.target.value })} />
              </div>
              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الحالة</label>
                <select className={`${selectClass} w-full`} value={editForm.status} data-testid="edit-status-select"
                  onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}>
                  <option value="present">حاضر</option>
                  <option value="late">متأخر</option>
                  <option value="absent">غائب</option>
                </select>
              </div>
              <div className="flex gap-3 justify-end">
                <Button variant="outline" onClick={() => setEditing(null)} className="rounded-sm">إلغاء</Button>
                <Button onClick={saveEdit} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="save-edit-btn">حفظ التعديل</Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Read-only audit log timeline - no edit/delete action exists here
            or anywhere else against attendance_audit_log. */}
        <Dialog open={auditLogOpen} onOpenChange={setAuditLogOpen}>
          <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto" data-testid="audit-log-dialog">
            <DialogHeader>
              <DialogTitle>سجل تعديلات الحضور</DialogTitle>
            </DialogHeader>
            {!auditLog ? (
              <div className="text-center py-8 text-gray-500 text-sm">Loading...</div>
            ) : auditLog.length === 0 ? (
              <div className="text-center py-8 text-gray-500 text-sm">لا توجد تعديلات مسجلة</div>
            ) : (
              <ol className="relative border-e-2 border-gray-200 me-2 space-y-6 py-2" data-testid="audit-log-timeline">
                {auditLog.map((entry) => {
                  const d = new Date(entry.edited_at);
                  return (
                    <li key={entry.id} className="ms-4" data-testid={`audit-entry-${entry.id}`}>
                      <span className="absolute w-2.5 h-2.5 bg-[#0033A0] rounded-full mt-1.5 -start-[5px] border border-white" />
                      <p className="text-xs text-gray-400">
                        {d.toLocaleDateString('ar-EG')} · {d.toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' })}
                      </p>
                      <p className="text-sm font-medium text-[#0A0A0A] mt-0.5">
                        {entry.edited_by_name || 'غير معروف'}
                        {entry.employee_name && <span className="text-gray-500 font-normal"> — سجل {entry.employee_name} ({entry.attendance_date})</span>}
                      </p>
                      <p className="text-xs text-gray-600 mt-1">{AUDIT_FIELD_LABELS[entry.field] || entry.field}</p>
                      <div className="flex items-center gap-2 mt-1 text-sm">
                        <span className="px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200 text-xs">{formatAuditValue(entry.field, entry.old_value)}</span>
                        <span className="text-gray-400">↓</span>
                        <span className="px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 text-xs">{formatAuditValue(entry.field, entry.new_value)}</span>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default OwnerAttendance;
