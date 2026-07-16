import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatDistanceToNow } from 'date-fns';
import { ar } from 'date-fns/locale';
import { Bell, CheckCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useNotifications } from '@/hooks/useNotifications';
import { categoryMeta, fallbackActionUrl } from '@/constants/notifications';

export const NotificationBell = ({ userRole, language }) => {
  const [open, setOpen] = useState(false);
  const { notifications, unreadCount, loading, live, markAsRead, markAllAsRead } = useNotifications(userRole);
  const navigate = useNavigate();

  const handleClick = (notification) => {
    if (!notification.read_status) markAsRead(notification.id);
    setOpen(false);
    navigate(notification.action_url || fallbackActionUrl(notification.category, userRole));
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="notification-bell-btn"
          className="relative flex items-center justify-center w-10 h-10 rounded-full hover:bg-gray-100 transition-colors"
          aria-label="الإشعارات"
        >
          <Bell className="w-5 h-5 text-gray-600" />
          {unreadCount > 0 && (
            <span
              data-testid="notification-unread-badge"
              className="absolute top-1 end-1 min-w-[18px] h-[18px] px-1 flex items-center justify-center text-[10px] font-bold text-white bg-red-500 rounded-full animate-in zoom-in-50"
            >
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align={language === 'ar' ? 'start' : 'end'}
        className="w-[380px] sm:w-[420px] p-0 max-h-[80vh] flex flex-col overflow-hidden"
        data-testid="notification-dropdown"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <h3 className="font-bold text-[#0A0A0A]">الإشعارات</h3>
            <span
              data-testid="notification-connection-state"
              title={live ? 'اتصال مباشر' : 'وضع التحديث الدوري'}
              className={`w-1.5 h-1.5 rounded-full ${live ? 'bg-emerald-500' : 'bg-gray-300'}`}
            />
          </div>
          {unreadCount > 0 && (
            <button
              onClick={markAllAsRead}
              data-testid="mark-all-read-btn"
              className="flex items-center gap-1 text-xs font-medium text-[#0033A0] hover:underline"
            >
              <CheckCheck className="w-3.5 h-3.5" />
              تحديد الكل كمقروء
            </button>
          )}
        </div>

        <ScrollArea className="flex-1 max-h-[65vh]">
          {loading ? (
            <div className="py-10 text-center text-sm text-gray-400">جارِ التحميل...</div>
          ) : notifications.length === 0 ? (
            <div className="py-12 px-4 text-center text-sm text-gray-400" data-testid="notification-empty-state">
              لا توجد إشعارات حتى الآن
            </div>
          ) : (
            <div>
              {notifications.map((n) => {
                const meta = categoryMeta(n.category);
                const Icon = meta.icon;
                return (
                  <button
                    key={n.id}
                    onClick={() => handleClick(n)}
                    data-testid={`notification-item-${n.id}`}
                    className={`w-full flex items-start gap-3 px-4 py-3 text-start border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors ${
                      !n.read_status ? 'bg-blue-50/50' : ''
                    }`}
                  >
                    <div className={`flex-shrink-0 p-2 rounded-full ${meta.bg}`}>
                      <Icon className={`w-4 h-4 ${meta.color}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className={`text-sm truncate ${!n.read_status ? 'font-bold text-[#0A0A0A]' : 'font-medium text-gray-700'}`}>
                          {n.title}
                        </p>
                        {!n.read_status && <span className="w-2 h-2 rounded-full bg-[#0033A0] flex-shrink-0" />}
                      </div>
                      <p className="text-xs text-gray-500 line-clamp-2 mt-0.5">{n.message}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[11px] text-gray-400">
                          {formatDistanceToNow(new Date(n.created_at), { addSuffix: true, locale: ar })}
                        </span>
                        {n.sender_name && (
                          <>
                            <span className="text-[11px] text-gray-300">•</span>
                            <span className="text-[11px] text-gray-400 truncate">{n.sender_name}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
};
