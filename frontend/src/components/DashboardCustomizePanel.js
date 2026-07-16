import { useEffect, useRef, useState } from 'react';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, Eye, EyeOff, X, RotateCcw, ChevronDown, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';

const SIZE_OPTIONS = [
  { value: 'sm', label: 'S', title: 'صغير' },
  { value: 'md', label: 'M', title: 'متوسط' },
  { value: 'lg', label: 'L', title: 'كبير' },
];

// Purely presentational and fully controlled by the parent page - `layout`
// is the live draft (not yet saved), `onChange` receives the whole new
// draft array on every edit. The parent re-renders the actual dashboard
// widgets from that same draft, which is what makes edits here show up as
// a live preview instead of only after Save. Deliberately NOT a modal
// dialog (no dark overlay, no focus trap) - the point of live preview is
// that the real dashboard stays fully visible while this panel is open.
// Advanced customization (Part 2) - each row can expand to reveal its
// widget's sub-elements (title, action button, individual charts, etc.),
// sourced from WIDGET_SECTIONS in Owner/Dashboard.js. A widget with no
// registered sections (none currently, but future-proof) simply has no
// expand chevron - whole-widget show/hide via the eye icon still works
// exactly as before regardless.
function SortableRow({ item, meta, sections, onToggleVisible, onSizeChange, onToggleSection, expanded, onToggleExpand }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.key });
  const style = {
    transform: transform ? `${CSS.Transform.toString(transform)} scale(${isDragging ? 1.02 : 1})` : undefined,
    transition,
    opacity: isDragging ? 0.9 : 1,
    boxShadow: isDragging ? '0 8px 20px -6px rgb(0 0 0 / 0.18)' : undefined,
    zIndex: isDragging ? 10 : undefined,
  };
  const Icon = meta?.icon;
  const hasSections = sections && sections.length > 0;

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid={`widget-row-${item.key}`}
      className={`bg-white border rounded-md mb-2 transition-shadow ${isDragging ? 'border-[#0033A0]/30' : 'border-gray-200 hover:shadow-sm'}`}
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          {...attributes}
          {...listeners}
          className="cursor-grab active:cursor-grabbing text-gray-400 hover:text-gray-600 touch-none"
          data-testid={`widget-drag-handle-${item.key}`}
        >
          <GripVertical className="w-4 h-4" />
        </button>
        {Icon && <Icon className="w-4 h-4 text-gray-500 flex-shrink-0" />}
        <span className="flex-1 text-sm font-medium text-gray-800 truncate">{meta?.title || item.key}</span>
        <div className="flex items-center gap-0.5 bg-gray-100 rounded-sm p-0.5" data-testid={`widget-size-group-${item.key}`}>
          {SIZE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              title={opt.title}
              onClick={() => onSizeChange(item.key, opt.value)}
              data-testid={`widget-size-${item.key}-${opt.value}`}
              className={`w-6 h-6 text-[11px] font-bold rounded-sm transition-colors ${
                (item.size || 'lg') === opt.value ? 'bg-[#0033A0] text-white' : 'text-gray-500 hover:bg-gray-200'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        {hasSections && (
          <button
            type="button"
            onClick={() => onToggleExpand(item.key)}
            data-testid={`widget-expand-${item.key}`}
            className="p-1.5 rounded-sm text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
            aria-label="تفاصيل إضافية"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={() => onToggleVisible(item.key)}
          data-testid={`widget-visibility-${item.key}`}
          className={`p-1.5 rounded-sm transition-colors ${item.visible ? 'text-[#0033A0] hover:bg-blue-50' : 'text-gray-300 hover:bg-gray-50'}`}
          aria-label={item.visible ? 'إخفاء' : 'إظهار'}
        >
          {item.visible ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
        </button>
      </div>
      {hasSections && expanded && (
        <div
          data-testid={`widget-sections-${item.key}`}
          className="px-3 pb-3 pt-1 ms-8 space-y-1.5 border-t border-gray-50 animate-in fade-in slide-in-from-top-1 duration-200"
        >
          {sections.map((s) => {
            const checked = (item.sections || {})[s.key] !== false;
            return (
              <label key={s.key} className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer py-0.5">
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => onToggleSection(item.key, s.key)}
                  data-testid={`widget-section-${item.key}-${s.key}`}
                />
                {s.label}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const DashboardCustomizePanel = ({ open, layout, registry, sectionsRegistry = {}, onChange, onSave, onCancel, onRestoreDefault, saving, language }) => {
  const panelRef = useRef(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));
  const isRTL = language === 'ar';
  const [expandedKey, setExpandedKey] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (e) => { if (e.key === 'Escape') onCancel(); };
    const handleClickOutside = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onCancel();
    };
    window.addEventListener('keydown', handleKeyDown);
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [open, onCancel]);

  if (!open || !layout) return null;

  const metaByKey = Object.fromEntries(registry.map((r) => [r.key, r]));
  const ordered = [...layout].sort((a, b) => a.order - b.order);

  const handleDragEnd = (event) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = ordered.findIndex((w) => w.key === active.id);
    const newIndex = ordered.findIndex((w) => w.key === over.id);
    onChange(arrayMove(ordered, oldIndex, newIndex).map((w, i) => ({ ...w, order: i })));
  };

  const toggleVisible = (key) => {
    onChange(layout.map((w) => (w.key === key ? { ...w, visible: !w.visible } : w)));
  };

  const changeSize = (key, size) => {
    onChange(layout.map((w) => (w.key === key ? { ...w, size } : w)));
  };

  const toggleSection = (key, sectionKey) => {
    onChange(layout.map((w) => {
      if (w.key !== key) return w;
      const current = w.sections || {};
      const wasVisible = current[sectionKey] !== false;
      return { ...w, sections: { ...current, [sectionKey]: !wasVisible } };
    }));
  };

  const toggleExpand = (key) => setExpandedKey((prev) => (prev === key ? null : key));

  return (
    <div
      ref={panelRef}
      data-testid="dashboard-customize-panel"
      className={`fixed inset-y-0 ${isRTL ? 'left-0' : 'right-0'} z-40 w-full sm:w-[400px] bg-white shadow-2xl border-gray-200 ${
        isRTL ? 'border-e' : 'border-s'
      } flex flex-col animate-in ${isRTL ? 'slide-in-from-left' : 'slide-in-from-right'} duration-300`}
    >
      <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
        <div>
          <h2 className="font-bold text-[#0A0A0A]">تخصيص لوحة التحكم</h2>
          <p className="text-xs text-gray-500 mt-1">التغييرات تظهر مباشرة في اللوحة خلفك</p>
        </div>
        <button onClick={onCancel} className="p-1.5 rounded-sm hover:bg-gray-100 text-gray-500" data-testid="customize-close-btn" aria-label="إغلاق">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={ordered.map((w) => w.key)} strategy={verticalListSortingStrategy}>
            {ordered.map((item) => (
              <SortableRow
                key={item.key}
                item={item}
                meta={metaByKey[item.key]}
                sections={sectionsRegistry[item.key]}
                expanded={expandedKey === item.key}
                onToggleExpand={toggleExpand}
                onToggleSection={toggleSection}
                onToggleVisible={toggleVisible}
                onSizeChange={changeSize}
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>

      <div className="flex items-center justify-between gap-2 px-5 py-4 border-t border-gray-100">
        <button
          type="button"
          onClick={onRestoreDefault}
          data-testid="customize-restore-default-btn"
          className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-700"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          استعادة الافتراضي
        </button>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} data-testid="customize-cancel-btn">
            إلغاء
          </Button>
          <Button
            size="sm"
            onClick={onSave}
            disabled={saving}
            data-testid="customize-save-btn"
            className="bg-[#0033A0] hover:bg-[#002277] text-white"
          >
            {saving ? 'جارِ الحفظ...' : 'حفظ'}
          </Button>
        </div>
      </div>
    </div>
  );
};
