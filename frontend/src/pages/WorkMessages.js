import { useEffect, useState, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import {
  Mail, Send, Archive as ArchiveIcon, Star, FileEdit, Paperclip, Reply, Forward,
  CheckCircle2, Pin, Bell, Search, Download, X, AlertTriangle, Clock, Siren,
  Users, Building2, ListChecks, ArrowDown,
} from 'lucide-react';
import { toast } from 'sonner';

// Duplicated deliberately from Employee/Tasks.js rather than extracted to a
// shared util - Task Management is on the do-not-modify list, so that file
// cannot be touched even for a harmless import change.
const playAlertSound = () => {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    [0, 0.25].forEach((delay) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.type = 'sine';
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.3, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.4);
      oscillator.start(ctx.currentTime + delay);
      oscillator.stop(ctx.currentTime + delay + 0.4);
    });
  } catch (e) { /* Web Audio not available - fail silently */ }
};

const PRIORITY_CONFIG = {
  normal: { label: 'عادي', color: 'text-gray-600', bg: 'bg-gray-50', border: 'border-gray-200', icon: Mail },
  important: { label: 'مهم', color: 'text-amber-700', bg: 'bg-amber-50', border: 'border-amber-200', icon: AlertTriangle },
  urgent: { label: 'عاجل', color: 'text-red-700', bg: 'bg-red-50', border: 'border-red-200', icon: Siren },
};
const CONFIDENTIALITY_LABELS = {
  public: 'عام', internal: 'داخلي', confidential: 'سري', highly_confidential: 'سري للغاية',
};
const SUGGESTED_TAGS = ['HR', 'Finance', 'Legal', 'Sales', 'Support', 'IT', 'Meeting', 'Report', 'Invoice', 'Project'];
const STATUS_LABELS = { delivered: 'تم الإرسال', seen: 'تمت المشاهدة', accepted: 'مقبولة', completed: 'مكتملة', archived: 'مؤرشفة' };

const formatDateTime = (iso) => iso ? new Date(iso).toLocaleString('ar-EG', { dateStyle: 'medium', timeStyle: 'short' }) : '-';

const PriorityBadge = ({ priority }) => {
  const cfg = PRIORITY_CONFIG[priority] || PRIORITY_CONFIG.normal;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${cfg.bg} ${cfg.color} ${cfg.border}`}>
      <Icon className="w-3 h-3" /> {cfg.label}
    </span>
  );
};

const emptyCompose = () => ({
  subject: '', body: '', priority: 'normal', confidentiality: 'internal', tags: [],
  recipientType: 'employee', recipientIds: [], requiresAck: false, completionRequired: false,
  expiresAt: '', isDraft: false, editingId: null,
});

const WorkMessages = ({ onLogout, language, setLanguage, userRole }) => {
  const [activeTab, setActiveTab] = useState('inbox');
  const [lists, setLists] = useState({ inbox: null, sent: null, drafts: null, starred: null, archived: null });
  const [loading, setLoading] = useState(true);
  const [employees, setEmployees] = useState([]);
  const [search, setSearch] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [page, setPage] = useState(1);
  const knownThreadIds = useRef(null);

  const [composeOpen, setComposeOpen] = useState(false);
  const [compose, setCompose] = useState(emptyCompose());
  const [pendingFiles, setPendingFiles] = useState([]);
  const [sending, setSending] = useState(false);

  const [threadOpen, setThreadOpen] = useState(false);
  const [thread, setThread] = useState(null);
  const [replyBody, setReplyBody] = useState('');
  const [forwardOpen, setForwardOpen] = useState(false);
  const [forwardRecipients, setForwardRecipients] = useState([]);
  const [reminderOpen, setReminderOpen] = useState(false);
  const [taskConvertOpen, setTaskConvertOpen] = useState(false);
  const [taskConvertForm, setTaskConvertForm] = useState({ assignedTo: '', dueDate: '' });

  const departments = [...new Set(employees.map((e) => e.department).filter(Boolean))];
  const isOwner = userRole === 'company_owner';
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    // Company-scoped recipient directory, accessible to owners AND employees.
    // Previously this used the owner-only /owner/employees endpoint, which
    // 403'd for employees and left their recipient dropdowns empty. The
    // /messages/recipients endpoint returns {employees, departments} for the
    // caller's own company; `departments` (line above) is derived from the
    // same employee.department values, matching send-time resolution.
    api.get('/messages/recipients')
      .then((res) => setEmployees(res.data.employees || []))
      .catch(() => {});
  }, []);

  // Additive-only deep link (?thread=<id>) for "Open Conversation" from the
  // Calendar page - does nothing when the param is absent, so every existing
  // way of arriving at this page is completely unaffected.
  useEffect(() => {
    const threadId = searchParams.get('thread');
    if (threadId) {
      openThread(threadId);
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const folderEndpoint = (folder) => `/messages/${folder}`;

  const fetchFolder = useCallback(async (folder, silent = false) => {
    if (!silent) setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '20' });
      if (search) params.set('subject', search);
      if (priorityFilter !== 'all') params.set('priority', priorityFilter);
      const res = await api.get(`${folderEndpoint(folder)}?${params.toString()}`);
      setLists((prev) => ({ ...prev, [folder]: res.data }));
      return res.data;
    } catch (e) {
      if (!silent) toast.error('خطأ في جلب الرسائل');
    } finally {
      if (!silent) setLoading(false);
    }
  }, [page, search, priorityFilter]);

  useEffect(() => { fetchFolder(activeTab); }, [activeTab, fetchFolder]);

  // Arrival detection: same 10s-poll-and-diff-known-IDs pattern already used
  // for urgent tasks - reuses the existing polling architecture, no WebSockets.
  useEffect(() => {
    const poll = async () => {
      const data = await fetchFolder('inbox', true);
      if (!data) return;
      const ids = new Set(data.items.map((m) => m.id));
      if (knownThreadIds.current !== null) {
        const arrived = [...ids].filter((id) => !knownThreadIds.current.has(id));
        if (arrived.length > 0) {
          const msg = data.items.find((m) => m.id === arrived[0]);
          toast.info(`📨 رسالة جديدة: ${msg?.subject || ''}`, { duration: 6000 });
          playAlertSound();
        }
      }
      knownThreadIds.current = ids;
      if (activeTab === 'inbox') setLists((prev) => ({ ...prev, inbox: data }));
    };
    poll();
    const interval = setInterval(poll, 10000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentList = lists[activeTab];

  // ---- Compose ----
  const openCompose = () => { setCompose(emptyCompose()); setPendingFiles([]); setComposeOpen(true); };

  const openDraftForEdit = (draft) => {
    setCompose({
      subject: draft.subject, body: draft.body, priority: draft.priority, confidentiality: draft.confidentiality,
      tags: draft.tags || [], recipientType: draft.recipient_type, recipientIds: [],
      requiresAck: draft.requires_acknowledgement, completionRequired: draft.completion_required,
      expiresAt: draft.expires_at || '', isDraft: true, editingId: draft.id,
    });
    setPendingFiles([]);
    setComposeOpen(true);
  };

  const addPendingFile = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setPendingFiles((prev) => [...prev, { filename: file.name, mime_type: file.type || 'application/octet-stream', data: reader.result }]);
    };
    reader.readAsDataURL(file);
    e.target.value = '';
  };

  const uploadPendingAttachments = async (messageId) => {
    for (const file of pendingFiles) {
      try {
        await api.post(`/messages/${messageId}/attachments`, file);
      } catch (e) {
        toast.error(`تعذر رفع ${file.filename}: ${e.response?.data?.detail || 'حدث خطأ'}`);
      }
    }
  };

  const submitCompose = async (asDraft) => {
    if (!compose.subject.trim() || !compose.body.trim()) { toast.error('العنوان والنص مطلوبان'); return; }
    if (!asDraft && compose.recipientIds.length === 0) { toast.error('يرجى اختيار مستلم واحد على الأقل'); return; }
    setSending(true);
    try {
      const payload = {
        subject: compose.subject, body: compose.body, priority: compose.priority,
        confidentiality: compose.confidentiality, tags: compose.tags,
        recipient_type: compose.recipientType, recipient_ids: compose.recipientIds,
        requires_acknowledgement: compose.requiresAck, completion_required: compose.completionRequired,
        expires_at: compose.expiresAt || null, is_draft: asDraft,
      };
      let messageId;
      if (compose.editingId) {
        await api.patch(`/messages/${compose.editingId}`, payload);
        messageId = compose.editingId;
        if (!asDraft) await api.post(`/messages/${messageId}/send`);
      } else {
        const res = await api.post('/messages', payload);
        messageId = res.data.id;
      }
      await uploadPendingAttachments(messageId);
      toast.success(asDraft ? 'تم حفظ المسودة' : 'تم إرسال الرسالة');
      setComposeOpen(false);
      fetchFolder(asDraft ? 'drafts' : 'sent');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
    setSending(false);
  };

  const toggleTag = (tag) => {
    setCompose((c) => ({ ...c, tags: c.tags.includes(tag) ? c.tags.filter((x) => x !== tag) : [...c.tags, tag] }));
  };

  // ---- Thread ----
  const openThread = async (messageId) => {
    try {
      const res = await api.get(`/messages/${messageId}`);
      setThread(res.data);
      setThreadOpen(true);
      await api.post(`/messages/${messageId}/open`);
      fetchFolder(activeTab, true);
    } catch (e) {
      toast.error('تعذر فتح الرسالة');
    }
  };

  const refreshThread = async () => {
    if (!thread) return;
    const res = await api.get(`/messages/${thread.thread_id}`);
    setThread(res.data);
    fetchFolder(activeTab, true);
  };

  const rootMessage = thread?.messages?.[0];

  const doAction = async (action, successMsg) => {
    try {
      await api.post(`/messages/${rootMessage.id}/${action}`);
      toast.success(successMsg);
      await refreshThread();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const sendReply = async () => {
    if (!replyBody.trim()) return;
    try {
      await api.post(`/messages/${rootMessage.id}/reply`, { body: replyBody });
      setReplyBody('');
      toast.success('تم إرسال الرد');
      await refreshThread();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const submitForward = async () => {
    if (forwardRecipients.length === 0) { toast.error('يرجى اختيار مستلم واحد على الأقل'); return; }
    try {
      await api.post(`/messages/${rootMessage.id}/forward`, {
        subject: rootMessage.subject, body: '', priority: rootMessage.priority,
        confidentiality: rootMessage.confidentiality, recipient_type: 'employee',
        recipient_ids: forwardRecipients, requires_acknowledgement: false, completion_required: false,
      });
      toast.success('تمت إعادة التوجيه');
      setForwardOpen(false);
      setForwardRecipients([]);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const submitReminder = async (preset) => {
    try {
      await api.post(`/messages/${rootMessage.id}/reminder`, preset === 'custom'
        ? { remind_at: new Date(document.getElementById('custom-reminder-input').value).toISOString() }
        : { preset });
      toast.success('تم ضبط التذكير');
      setReminderOpen(false);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const submitConvertToTask = async () => {
    if (!taskConvertForm.assignedTo || !taskConvertForm.dueDate) { toast.error('يرجى اختيار الموظف والتاريخ'); return; }
    try {
      // Reuses the existing task-creation endpoint directly - no task logic
      // is reimplemented here, only a prefilled call to it.
      await api.post('/owner/tasks', {
        title: rootMessage.subject, description: rootMessage.body, priority: 'medium',
        assigned_to: taskConvertForm.assignedTo, due_date: taskConvertForm.dueDate, requires_proof: false,
      });
      toast.success('تم إنشاء مهمة من الرسالة');
      setTaskConvertOpen(false);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ');
    }
  };

  const downloadAttachment = async (attachmentId, filename) => {
    try {
      const res = await api.get(`/message-attachments/${attachmentId}`);
      const link = document.createElement('a');
      link.href = res.data.data;
      link.download = filename;
      link.click();
    } catch (e) {
      toast.error('تعذر تحميل المرفق');
    }
  };

  const tabs = [
    { key: 'inbox', label: 'الوارد', icon: Mail },
    { key: 'sent', label: 'المرسل', icon: Send },
    { key: 'starred', label: 'المميزة', icon: Star },
    { key: 'drafts', label: 'المسودات', icon: FileEdit },
    { key: 'archived', label: 'الأرشيف', icon: ArchiveIcon },
  ];

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-start flex-wrap gap-4">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="work-messages-title">رسائل العمل</h1>
            <p className="text-gray-600 mt-2">نظام تواصل داخلي منظم للأعمال</p>
          </div>
          <Button onClick={openCompose} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="compose-btn">
            <Mail className="w-4 h-4 me-2" /> رسالة جديدة
          </Button>
        </div>

        <Card className="p-4 bg-white border border-gray-200 rounded-md">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[200px]">
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">بحث بالعنوان</label>
              <div className="relative">
                <Search className="w-4 h-4 absolute top-1/2 -translate-y-1/2 start-3 text-gray-400" />
                <Input data-testid="message-search-input" value={search} onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && fetchFolder(activeTab)} className="ps-9" />
              </div>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الأولوية</label>
              <Select value={priorityFilter} onValueChange={(v) => { setPriorityFilter(v); }}>
                <SelectTrigger className="w-36" data-testid="priority-filter"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">الكل</SelectItem>
                  <SelectItem value="normal">عادي</SelectItem>
                  <SelectItem value="important">مهم</SelectItem>
                  <SelectItem value="urgent">عاجل</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button variant="outline" onClick={() => fetchFolder(activeTab)} className="rounded-sm">تطبيق</Button>
          </div>
        </Card>

        <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v); setPage(1); }} dir={language === 'ar' ? 'rtl' : 'ltr'}>
          <TabsList className="flex-wrap h-auto">
            {tabs.map((tb) => {
              const Icon = tb.icon;
              return (
                <TabsTrigger key={tb.key} value={tb.key} data-testid={`tab-${tb.key}`}>
                  <Icon className="w-4 h-4 me-1.5" /> {tb.label}
                  {tb.key === 'inbox' && lists.inbox?.items?.some((m) => m.my_is_unread) && (
                    <span className="ms-1.5 w-2 h-2 rounded-full bg-red-500 inline-block" />
                  )}
                </TabsTrigger>
              );
            })}
          </TabsList>

          <TabsContent value={activeTab} className="mt-6">
            {loading ? (
              <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : !currentList || currentList.items.length === 0 ? (
              <Card className="p-12 text-center bg-white border border-gray-200">
                <Mail className="w-12 h-12 mx-auto text-gray-300 mb-4" />
                <p className="text-gray-500">لا توجد رسائل</p>
              </Card>
            ) : (
              <>
                <Card className="bg-white border border-gray-200 rounded-md shadow-sm divide-y divide-gray-100">
                  {currentList.items.map((m) => (
                    <div
                      key={m.id}
                      onClick={() => activeTab === 'drafts' ? openDraftForEdit(m) : openThread(m.id)}
                      className={`p-4 cursor-pointer hover:bg-gray-50/70 transition-colors ${m.my_is_unread ? 'bg-blue-50/40' : ''}`}
                      data-testid={`message-row-${m.id}`}
                    >
                      <div className="flex items-start justify-between gap-4 flex-wrap">
                        <div className="flex items-start gap-3 min-w-0">
                          {m.is_pinned && <Pin className="w-4 h-4 text-[#0033A0] mt-1 shrink-0" />}
                          <div className="min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-xs text-gray-400 font-mono">{m.reference_number}</span>
                              <PriorityBadge priority={m.priority} />
                              {m.is_expired && <span className="text-xs text-red-600">منتهي الصلاحية</span>}
                              {m.closed_at && <span className="text-xs text-gray-400">مغلقة</span>}
                            </div>
                            <p className={`mt-1 ${m.my_is_unread ? 'font-bold' : 'font-medium'} text-[#0A0A0A] truncate`}>{m.subject}</p>
                            <p className="text-xs text-gray-500 mt-0.5">
                              {activeTab === 'sent' || activeTab === 'drafts' ? (m.recipient_names || []).join('، ') || m.recipient_department : m.sender_name}
                              {m.attachment_count > 0 && <span className="ms-2"><Paperclip className="w-3 h-3 inline" /> {m.attachment_count}</span>}
                              {m.reply_count > 0 && <span className="ms-2">{m.reply_count} رد</span>}
                            </p>
                          </div>
                        </div>
                        <div className="text-end shrink-0">
                          <p className="text-xs text-gray-400">{formatDateTime(m.created_at)}</p>
                          {m.delivery_progress && m.delivery_progress.total > 1 && (
                            <p className="text-xs text-gray-500 mt-1">
                              وصلت {m.delivery_progress.delivered}/{m.delivery_progress.total} · شوهدت {m.delivery_progress.seen}/{m.delivery_progress.total}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </Card>
                {currentList.total > currentList.page_size && (
                  <div className="flex justify-center gap-2 mt-4">
                    <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>السابق</Button>
                    <span className="text-sm text-gray-500 self-center">صفحة {page} من {Math.ceil(currentList.total / currentList.page_size)}</span>
                    <Button variant="outline" size="sm" disabled={page * currentList.page_size >= currentList.total} onClick={() => setPage((p) => p + 1)}>التالي</Button>
                  </div>
                )}
              </>
            )}
          </TabsContent>
        </Tabs>

        {/* ============ Compose Dialog ============ */}
        <Dialog open={composeOpen} onOpenChange={setComposeOpen}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="compose-dialog">
            <DialogHeader><DialogTitle>{compose.editingId ? 'تعديل المسودة' : 'رسالة عمل جديدة'}</DialogTitle></DialogHeader>
            <div className="space-y-4">
              <Input placeholder="العنوان" value={compose.subject} data-testid="compose-subject"
                onChange={(e) => setCompose({ ...compose, subject: e.target.value })} />
              <Textarea placeholder="نص الرسالة" rows={5} value={compose.body} data-testid="compose-body"
                onChange={(e) => setCompose({ ...compose, body: e.target.value })} />

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الأولوية</label>
                  <Select value={compose.priority} onValueChange={(v) => setCompose({ ...compose, priority: v })}>
                    <SelectTrigger data-testid="compose-priority"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="normal">عادي</SelectItem>
                      <SelectItem value="important">مهم</SelectItem>
                      <SelectItem value="urgent">عاجل</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">مستوى السرية</label>
                  <Select value={compose.confidentiality} onValueChange={(v) => setCompose({ ...compose, confidentiality: v })}>
                    <SelectTrigger data-testid="compose-confidentiality"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(CONFIDENTIALITY_LABELS).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">الوسوم</label>
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTED_TAGS.map((tag) => (
                    <button key={tag} type="button" onClick={() => toggleTag(tag)}
                      className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
                        compose.tags.includes(tag) ? 'bg-[#0033A0] text-white border-[#0033A0]' : 'bg-white text-gray-600 border-gray-200'
                      }`}>
                      {tag}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">إرسال إلى</label>
                  <Select value={compose.recipientType} onValueChange={(v) => setCompose({ ...compose, recipientType: v, recipientIds: [] })}>
                    <SelectTrigger data-testid="compose-recipient-type"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="employee"><Users className="w-3.5 h-3.5 inline me-1" /> موظف / موظفين</SelectItem>
                      <SelectItem value="department"><Building2 className="w-3.5 h-3.5 inline me-1" /> قسم كامل</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">
                    {compose.recipientType === 'department' ? 'القسم' : 'المستلمون'}
                  </label>
                  {compose.recipientType === 'department' ? (
                    <Select value={compose.recipientIds[0] || ''} onValueChange={(v) => setCompose({ ...compose, recipientIds: [v] })}>
                      <SelectTrigger data-testid="compose-department"><SelectValue placeholder="اختر القسم" /></SelectTrigger>
                      <SelectContent>{departments.map((d) => <SelectItem key={d} value={d}>{d}</SelectItem>)}</SelectContent>
                    </Select>
                  ) : (
                    <div className="border border-gray-200 rounded-md max-h-32 overflow-y-auto p-2 space-y-1" data-testid="compose-employee-list">
                      {employees.map((emp) => (
                        <label key={emp.id} className="flex items-center gap-2 text-sm">
                          <Checkbox checked={compose.recipientIds.includes(emp.id)}
                            onCheckedChange={(checked) => setCompose((c) => ({
                              ...c, recipientIds: checked ? [...c.recipientIds, emp.id] : c.recipientIds.filter((id) => id !== emp.id),
                            }))} />
                          {emp.name}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap gap-6">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={compose.requiresAck} data-testid="compose-requires-ack"
                    onCheckedChange={(v) => setCompose({ ...compose, requiresAck: !!v })} /> يتطلب إقرار
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={compose.completionRequired} data-testid="compose-completion-required"
                    onCheckedChange={(v) => setCompose({ ...compose, completionRequired: !!v })} /> يتطلب إنجاز
                </label>
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-600">تاريخ الانتهاء (اختياري)</label>
                  <Input type="date" value={compose.expiresAt?.slice(0, 10) || ''} className="w-40" data-testid="compose-expires-at"
                    onChange={(e) => setCompose({ ...compose, expiresAt: e.target.value ? `${e.target.value}T23:59:59+00:00` : '' })} />
                </div>
              </div>

              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500 mb-1 block">المرفقات</label>
                <input type="file" onChange={addPendingFile} className="text-sm" data-testid="compose-file-input" />
                {pendingFiles.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {pendingFiles.map((f, i) => (
                      <li key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-2 py-1">
                        <span><Paperclip className="w-3 h-3 inline me-1" /> {f.filename}</span>
                        <button onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}><X className="w-3 h-3" /></button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex justify-end gap-3 pt-2 border-t border-gray-100">
                <Button variant="outline" onClick={() => submitCompose(true)} disabled={sending} className="rounded-sm" data-testid="save-draft-btn">
                  حفظ كمسودة
                </Button>
                <Button onClick={() => submitCompose(false)} disabled={sending} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="send-message-btn">
                  <Send className="w-4 h-4 me-2" /> إرسال
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* ============ Thread Dialog ============ */}
        <Dialog open={threadOpen} onOpenChange={setThreadOpen}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="thread-dialog">
            {rootMessage && (
              <>
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-gray-400 font-mono">{rootMessage.reference_number}</span>
                    {rootMessage.subject}
                    <PriorityBadge priority={rootMessage.priority} />
                  </DialogTitle>
                </DialogHeader>

                <div className="flex flex-wrap gap-2 text-xs text-gray-500 border-b border-gray-100 pb-3">
                  <span>السرية: {CONFIDENTIALITY_LABELS[rootMessage.confidentiality]}</span>
                  {rootMessage.tags?.length > 0 && <span>· الوسوم: {rootMessage.tags.join('، ')}</span>}
                  {rootMessage.expires_at && <span>· ينتهي: {formatDateTime(rootMessage.expires_at)}</span>}
                  {rootMessage.closed_at && <span className="text-red-600">· مغلقة</span>}
                </div>

                {rootMessage.delivery_progress && rootMessage.delivery_progress.total > 0 && (
                  <div className="grid grid-cols-4 gap-2 text-center text-xs bg-gray-50 rounded-md p-2" data-testid="thread-delivery-progress">
                    <div><p className="font-bold text-[#0A0A0A]">{rootMessage.delivery_progress.delivered}/{rootMessage.delivery_progress.total}</p><p className="text-gray-500">وصلت</p></div>
                    <div><p className="font-bold text-[#0A0A0A]">{rootMessage.delivery_progress.seen}/{rootMessage.delivery_progress.total}</p><p className="text-gray-500">شوهدت</p></div>
                    <div><p className="font-bold text-[#0A0A0A]">{rootMessage.delivery_progress.accepted}/{rootMessage.delivery_progress.total}</p><p className="text-gray-500">قُبلت</p></div>
                    <div><p className="font-bold text-[#0A0A0A]">{rootMessage.delivery_progress.completed}/{rootMessage.delivery_progress.total}</p><p className="text-gray-500">أُنجزت</p></div>
                  </div>
                )}

                <div className="space-y-3 max-h-64 overflow-y-auto">
                  {thread.messages.map((m) => (
                    <div key={m.id} className="border border-gray-100 rounded-md p-3" data-testid={`thread-message-${m.id}`}>
                      <div className="flex justify-between text-xs text-gray-500 mb-1">
                        <span className="font-medium text-[#0A0A0A]">{m.sender_name}</span>
                        <span>{formatDateTime(m.created_at)}</span>
                      </div>
                      <p className="text-sm text-gray-700 whitespace-pre-wrap">{m.body}</p>
                      {m.attachments?.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {m.attachments.map((a) => (
                            <button key={a.id} onClick={() => downloadAttachment(a.id, a.filename)}
                              className="flex items-center gap-1 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1 hover:bg-gray-100">
                              <Download className="w-3 h-3" /> {a.filename}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap gap-2 border-t border-gray-100 pt-3">
                  <Button size="sm" variant="outline" className="rounded-sm" onClick={() => doAction('star', 'تم التحديث')} data-testid="thread-star-btn">
                    <Star className="w-3.5 h-3.5 me-1" /> {rootMessage.my_is_starred ? 'إلغاء التمييز' : 'تمييز'}
                  </Button>
                  <Button size="sm" variant="outline" className="rounded-sm" onClick={() => doAction('archive', 'تمت الأرشفة')} data-testid="thread-archive-btn">
                    <ArchiveIcon className="w-3.5 h-3.5 me-1" /> أرشفة
                  </Button>
                  <Button size="sm" variant="outline" className="rounded-sm" onClick={() => doAction('unread', 'تم التحديد كغير مقروءة')} data-testid="thread-unread-btn">
                    غير مقروءة
                  </Button>
                  {rootMessage.requires_acknowledgement && rootMessage.my_status && ['delivered', 'seen'].includes(rootMessage.my_status) && (
                    <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-sm" onClick={() => doAction('accept', 'تم القبول')} data-testid="thread-accept-btn">
                      <CheckCircle2 className="w-3.5 h-3.5 me-1" /> قبول
                    </Button>
                  )}
                  {rootMessage.completion_required && rootMessage.my_status !== 'completed' && (
                    <Button size="sm" className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" onClick={() => doAction('complete', 'تم الإنجاز')} data-testid="thread-complete-btn">
                      <ListChecks className="w-3.5 h-3.5 me-1" /> إنجاز
                    </Button>
                  )}
                  <Button size="sm" variant="outline" className="rounded-sm" onClick={() => setForwardOpen(true)} data-testid="thread-forward-btn">
                    <Forward className="w-3.5 h-3.5 me-1" /> إعادة توجيه
                  </Button>
                  <Button size="sm" variant="outline" className="rounded-sm" onClick={() => setReminderOpen(true)} data-testid="thread-reminder-btn">
                    <Bell className="w-3.5 h-3.5 me-1" /> تذكير
                  </Button>
                  {isOwner && (
                    <Button size="sm" variant="outline" className="rounded-sm" onClick={() => doAction('pin', 'تم التحديث')} data-testid="thread-pin-btn">
                      <Pin className="w-3.5 h-3.5 me-1" /> {rootMessage.is_pinned ? 'إلغاء التثبيت' : 'تثبيت'}
                    </Button>
                  )}
                  {isOwner && (
                    <Button size="sm" variant="outline" className="rounded-sm" onClick={() => setTaskConvertOpen(true)} data-testid="thread-convert-task-btn">
                      تحويل إلى مهمة
                    </Button>
                  )}
                  {!rootMessage.closed_at && (
                    <Button size="sm" variant="outline" className="rounded-sm text-red-600" onClick={() => doAction('close', 'تم إغلاق المحادثة')} data-testid="thread-close-btn">
                      إغلاق المحادثة
                    </Button>
                  )}
                </div>

                {!rootMessage.closed_at && (
                  <div className="flex gap-2 pt-2">
                    <Textarea placeholder="اكتب رداً..." rows={2} value={replyBody} onChange={(e) => setReplyBody(e.target.value)}
                      className="flex-1" data-testid="reply-textarea" />
                    <Button onClick={sendReply} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm self-end" data-testid="send-reply-btn">
                      <Reply className="w-4 h-4" />
                    </Button>
                  </div>
                )}
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* Forward mini-dialog */}
        <Dialog open={forwardOpen} onOpenChange={setForwardOpen}>
          <DialogContent className="max-w-md" data-testid="forward-dialog">
            <DialogHeader><DialogTitle>إعادة توجيه داخل الشركة</DialogTitle></DialogHeader>
            <div className="border border-gray-200 rounded-md max-h-48 overflow-y-auto p-2 space-y-1">
              {employees.map((emp) => (
                <label key={emp.id} className="flex items-center gap-2 text-sm">
                  <Checkbox checked={forwardRecipients.includes(emp.id)}
                    onCheckedChange={(checked) => setForwardRecipients((prev) => checked ? [...prev, emp.id] : prev.filter((id) => id !== emp.id))} />
                  {emp.name}
                </label>
              ))}
            </div>
            <Button onClick={submitForward} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="submit-forward-btn">إرسال</Button>
          </DialogContent>
        </Dialog>

        {/* Reminder mini-dialog */}
        <Dialog open={reminderOpen} onOpenChange={setReminderOpen}>
          <DialogContent className="max-w-sm" data-testid="reminder-dialog">
            <DialogHeader><DialogTitle>ضبط تذكير</DialogTitle></DialogHeader>
            <div className="space-y-2">
              <Button variant="outline" className="w-full rounded-sm" onClick={() => submitReminder('30m')}>بعد 30 دقيقة</Button>
              <Button variant="outline" className="w-full rounded-sm" onClick={() => submitReminder('1h')}>بعد ساعة</Button>
              <Button variant="outline" className="w-full rounded-sm" onClick={() => submitReminder('tomorrow')}>غداً 9:00 ص</Button>
              <div className="flex gap-2">
                <Input id="custom-reminder-input" type="datetime-local" className="flex-1" />
                <Button variant="outline" onClick={() => submitReminder('custom')}>تحديد</Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Convert-to-task mini-dialog (owner only, reuses POST /owner/tasks) */}
        <Dialog open={taskConvertOpen} onOpenChange={setTaskConvertOpen}>
          <DialogContent className="max-w-sm" data-testid="convert-task-dialog">
            <DialogHeader><DialogTitle>تحويل إلى مهمة</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <Select value={taskConvertForm.assignedTo} onValueChange={(v) => setTaskConvertForm({ ...taskConvertForm, assignedTo: v })}>
                <SelectTrigger data-testid="convert-task-assignee"><SelectValue placeholder="تعيين إلى" /></SelectTrigger>
                <SelectContent>{employees.map((emp) => <SelectItem key={emp.id} value={emp.id}>{emp.name}</SelectItem>)}</SelectContent>
              </Select>
              <Input type="date" value={taskConvertForm.dueDate} data-testid="convert-task-due-date"
                onChange={(e) => setTaskConvertForm({ ...taskConvertForm, dueDate: e.target.value })} />
              <Button onClick={submitConvertToTask} className="w-full bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="submit-convert-task-btn">
                إنشاء المهمة
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
};

export default WorkMessages;
