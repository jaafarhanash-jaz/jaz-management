import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, User, Clock, CalendarClock, Paperclip } from 'lucide-react';
import api from '@/utils/api';
import { toast } from 'sonner';

// Duplicated from Employee/Tasks.js / WorkMessages.js rather than imported -
// Task Management is off-limits to modify, and this needs a more insistent
// (repeating, louder) pattern anyway.
const playCriticalAlertSound = () => {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    [0, 0.3, 0.6].forEach((delay) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.type = 'square';
      oscillator.frequency.value = 1000;
      gain.gain.setValueAtTime(0.35, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.25);
      oscillator.start(ctx.currentTime + delay);
      oscillator.stop(ctx.currentTime + delay + 0.25);
    });
  } catch (e) { /* Web Audio not available - fail silently */ }
};

const formatDateTime = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('ar', { dateStyle: 'medium', timeStyle: 'short' });
  } catch (e) { return iso; }
};

// Global full-screen overlay for Urgent Task alerts (user-facing label;
// internally still priority="critical"). Mounted once in App.js, fed by the
// existing heartbeat poll's pending_critical_tasks list - no new polling
// loop. Renders nothing when the queue is empty.
const CriticalTaskAlert = ({ tasks, onHandled }) => {
  const [busy, setBusy] = useState(false);
  const soundInterval = useRef(null);
  const currentTask = tasks && tasks.length > 0 ? tasks[0] : null;

  const startSiren = () => {
    playCriticalAlertSound();
    if (soundInterval.current) clearInterval(soundInterval.current);
    soundInterval.current = setInterval(playCriticalAlertSound, 3000);
    if ('vibrate' in navigator) {
      navigator.vibrate([500, 200, 500, 200, 500]);
    }
  };

  const stopSiren = () => {
    if (soundInterval.current) {
      clearInterval(soundInterval.current);
      soundInterval.current = null;
    }
    if ('vibrate' in navigator) navigator.vibrate(0);
  };

  useEffect(() => {
    if (!currentTask) return;

    startSiren();

    if ('Notification' in window) {
      if (Notification.permission === 'granted') {
        try {
          new Notification('مهمة عاجلة', {
            body: currentTask.title,
            requireInteraction: true,
            tag: `critical-task-${currentTask.id}`
          });
        } catch (e) { /* ignore */ }
      } else if (Notification.permission !== 'denied') {
        Notification.requestPermission();
      }
    }

    return () => stopSiren();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTask?.id]);

  // No dismiss path on purpose: no Escape key handler, no outside-click
  // handler, no close button, no history/back-navigation hook. Only
  // Receive/Start remove a task from the queue.

  const handleAction = async (action) => {
    if (!currentTask || busy) return;
    stopSiren();
    setBusy(true);
    try {
      if (action === 'receive') {
        await api.post(`/employee/tasks/${currentTask.id}/receive`);
      } else {
        await api.put(`/employee/tasks/${currentTask.id}/status`, { status: 'in_progress' });
      }
      onHandled(currentTask.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'تعذر تنفيذ الإجراء، يرجى المحاولة مرة أخرى');
      startSiren();
    }
    setBusy(false);
  };

  if (!currentTask) return null;

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-red-950 p-4"
      role="alertdialog"
      aria-modal="true"
      data-testid="critical-task-alert-overlay"
    >
      <div className="absolute inset-0 bg-red-700 animate-pulse opacity-20" />
      <div className="relative z-10 max-w-xl w-full text-center text-white">
        <AlertTriangle className="w-16 h-16 md:w-24 md:h-24 mx-auto mb-4 md:mb-6 text-yellow-300 animate-pulse" />
        <div className="text-xs md:text-sm font-bold tracking-widest text-red-200 mb-2">تنبيه طارئ - لا يمكن إغلاق هذه الشاشة</div>
        <h1 className="text-2xl md:text-4xl font-extrabold mb-4 md:mb-6">مهمة عاجلة</h1>

        <div className="bg-white/10 backdrop-blur-sm rounded-lg p-4 md:p-6 mb-4 text-right space-y-3" data-testid="critical-task-alert-details">
          <div className="text-lg md:text-2xl font-bold">{currentTask.title}</div>
          {currentTask.description && <div className="text-red-100 text-sm md:text-base whitespace-pre-wrap">{currentTask.description}</div>}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2 border-t border-white/20 text-xs md:text-sm text-red-100">
            {currentTask.created_by_name && (
              <div className="flex items-center gap-2">
                <User className="w-4 h-4 flex-shrink-0" />
                <span>من: {currentTask.created_by_name}</span>
              </div>
            )}
            {currentTask.created_at && (
              <div className="flex items-center gap-2">
                <Clock className="w-4 h-4 flex-shrink-0" />
                <span>وقت الإرسال: {formatDateTime(currentTask.created_at)}</span>
              </div>
            )}
            {currentTask.due_date && (
              <div className="flex items-center gap-2 sm:col-span-2">
                <CalendarClock className="w-4 h-4 flex-shrink-0" />
                <span>الموعد النهائي: {currentTask.due_date}{currentTask.due_time ? ` - ${currentTask.due_time}` : ''}</span>
              </div>
            )}
          </div>

          {currentTask.attachments && currentTask.attachments.length > 0 && (
            <div className="pt-2 border-t border-white/20">
              <div className="text-xs text-red-200 mb-1">المرفقات</div>
              <div className="flex flex-wrap gap-2">
                {currentTask.attachments.map((f, i) => (
                  <a
                    key={i}
                    href={f.data}
                    download={f.filename}
                    className="flex items-center gap-1 text-xs bg-white/10 hover:bg-white/20 rounded px-2 py-1 transition-colors"
                    data-testid={`critical-task-alert-attachment-${i}`}
                  >
                    <Paperclip className="w-3 h-3" /> {f.filename}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>

        {tasks.length > 1 && (
          <div className="text-red-200 text-xs mb-4">مهمة 1 من {tasks.length} - يوجد المزيد من المهام العاجلة بانتظارك</div>
        )}

        <div className="flex flex-col sm:flex-row gap-3 justify-center mt-4">
          <button
            onClick={() => handleAction('receive')}
            disabled={busy}
            data-testid="critical-task-receive-btn"
            className="px-6 py-3 rounded-lg bg-white text-red-800 font-bold hover:bg-red-50 disabled:opacity-50 transition-colors"
          >
            استلام المهمة
          </button>
          <button
            onClick={() => handleAction('start')}
            disabled={busy}
            data-testid="critical-task-start-btn"
            className="px-6 py-3 rounded-lg bg-yellow-400 text-red-900 font-bold hover:bg-yellow-300 disabled:opacity-50 transition-colors"
          >
            بدء العمل الآن
          </button>
        </div>
      </div>
    </div>
  );
};

export default CriticalTaskAlert;
