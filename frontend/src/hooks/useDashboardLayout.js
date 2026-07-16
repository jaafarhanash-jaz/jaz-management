import { useCallback, useEffect, useState } from 'react';
import api from '@/utils/api';

// Persists the customizable-dashboard widget layout (Part 2) in Postgres,
// one row per user (see backend/services/dashboard.py). `registry` is the
// page's static list of {key, ...} widgets it knows how to render - this
// hook's only job is reconciling that registry against whatever the user
// last saved. Any registry key missing from the saved layout (first-ever
// load, or a widget shipped after the user's last save) is appended as
// visible in registry order, so shipping a new widget never requires a
// migration or a backend change - purely a frontend addition.
// Default size is "lg" (full width) - matches the pre-resizing layout, so
// nothing looks different until a user actually resizes something.
export function buildDefaultLayout(registry) {
  return registry.map((r, i) => ({ key: r.key, visible: true, order: i, size: 'lg' }));
}

export function useDashboardLayout(registry) {
  const [layout, setLayout] = useState(null);
  const [saving, setSaving] = useState(false);

  const reconcile = useCallback((saved) => {
    const savedKeys = new Set(saved.map((w) => w.key));
    const validKeys = new Set(registry.map((r) => r.key));
    const kept = saved.filter((w) => validKeys.has(w.key)).map((w) => ({ size: 'lg', ...w }));
    const appended = registry
      .filter((r) => !savedKeys.has(r.key))
      .map((r, i) => ({ key: r.key, visible: true, order: kept.length + i, size: 'lg' }));
    return [...kept, ...appended].sort((a, b) => a.order - b.order);
  }, [registry]);

  useEffect(() => {
    let cancelled = false;
    api.get('/dashboard/layout')
      .then((res) => {
        if (cancelled) return;
        setLayout(reconcile(res.data.layout || []));
      })
      .catch(() => {
        if (!cancelled) setLayout(buildDefaultLayout(registry));
      });
    return () => { cancelled = true; };
  }, [reconcile, registry]);

  const save = useCallback(async (newLayout) => {
    setSaving(true);
    try {
      const res = await api.put('/dashboard/layout', { layout: newLayout });
      setLayout(res.data.layout);
      return true;
    } catch (error) {
      console.error('Error saving dashboard layout:', error);
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

  return { layout, save, saving };
}
