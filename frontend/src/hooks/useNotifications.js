import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import api from '@/utils/api';
import { categoryMeta } from '@/constants/notifications';

const POLL_INTERVAL_MS = 10000;
const SSE_CONNECT_GRACE_MS = 4000;
const API_URL = process.env.REACT_APP_BACKEND_URL + '/api';

// Real-time delivery: Server-Sent Events (see GET /notifications/stream,
// services/realtime.py) is the primary channel - the backend pushes a
// notification the instant services.notifications.publish() creates it, no
// interval involved. Polling every POLL_INTERVAL_MS is only the fallback,
// active exclusively while the SSE connection is not open (initial
// handshake, a dropped connection, a proxy/network that blocks
// text/event-stream). The moment SSE reports open again, polling stops.
// EventSource's own built-in reconnect (governed by the `retry:` field the
// server sends) handles re-establishing the connection - this hook just
// reacts to open/error, it never manually recreates the EventSource.
export function useNotifications(userRole) {
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const seenIds = useRef(new Set());

  const notifyToast = useCallback((n) => {
    const meta = categoryMeta(n.category);
    toast(n.title, { description: n.message, icon: <meta.icon className={`w-4 h-4 ${meta.color}`} /> });
  }, []);

  const fetchNotifications = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get('/notifications');
      const fresh = res.data;

      if (silent) {
        // Only the polling fallback path re-diffs for toasts - SSE messages
        // are already known-new by construction, so they toast on arrival
        // instead (see handleIncoming below).
        const newlyArrived = fresh.filter((n) => !n.read_status && !seenIds.current.has(n.id));
        newlyArrived.slice(0, 3).forEach(notifyToast);
      }
      seenIds.current = new Set(fresh.map((n) => n.id));
      setNotifications(fresh);
    } catch (error) {
      console.error('Error fetching notifications:', error);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [notifyToast]);

  const handleIncoming = useCallback((notification) => {
    if (seenIds.current.has(notification.id)) return;
    seenIds.current.add(notification.id);
    setNotifications((prev) => [notification, ...prev]);
    if (!notification.read_status) notifyToast(notification);
  }, [notifyToast]);

  useEffect(() => {
    if (userRole !== 'company_owner' && userRole !== 'employee' && userRole !== 'super_admin') return undefined;

    let cancelled = false;
    let pollTimer = null;
    let graceTimer = null;
    let eventSource = null;

    const startPolling = () => {
      if (pollTimer || cancelled) return;
      setLive(false);
      pollTimer = setInterval(() => fetchNotifications(true), POLL_INTERVAL_MS);
    };
    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    fetchNotifications();

    const token = localStorage.getItem('token');
    if (token && typeof window.EventSource !== 'undefined') {
      try {
        eventSource = new EventSource(`${API_URL}/notifications/stream?token=${encodeURIComponent(token)}`);
        graceTimer = setTimeout(() => {
          if (!cancelled && eventSource.readyState !== EventSource.OPEN) startPolling();
        }, SSE_CONNECT_GRACE_MS);

        eventSource.onopen = () => {
          if (cancelled) return;
          clearTimeout(graceTimer);
          stopPolling();
          setLive(true);
        };
        eventSource.onmessage = (event) => {
          if (cancelled || !event.data) return;
          try {
            handleIncoming(JSON.parse(event.data));
          } catch (error) {
            console.error('Error parsing SSE notification payload:', error);
          }
        };
        eventSource.onerror = () => {
          if (cancelled) return;
          startPolling();
        };
      } catch (error) {
        console.error('EventSource unavailable, falling back to polling:', error);
        startPolling();
      }
    } else {
      startPolling();
    }

    return () => {
      cancelled = true;
      clearTimeout(graceTimer);
      stopPolling();
      if (eventSource) eventSource.close();
    };
  }, [userRole, fetchNotifications, handleIncoming]);

  const unreadCount = notifications.filter((n) => !n.read_status).length;

  const markAsRead = useCallback(async (id) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read_status: true } : n)));
    try {
      await api.put(`/notifications/${id}/read`);
    } catch (error) {
      console.error('Error marking notification read:', error);
    }
  }, []);

  const markAllAsRead = useCallback(async () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read_status: true })));
    try {
      await api.put('/notifications/read-all');
    } catch (error) {
      console.error('Error marking all notifications read:', error);
    }
  }, []);

  return { notifications, unreadCount, loading, live, markAsRead, markAllAsRead, refresh: () => fetchNotifications(true) };
}
