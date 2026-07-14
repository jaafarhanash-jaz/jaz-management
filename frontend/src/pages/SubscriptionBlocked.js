import { useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { AlertTriangle, Ban, MessageCircle, Mail, LogIn } from 'lucide-react';

const WHATSAPP_SUPPORT_URL = 'https://wa.me/000000000000';
const SUPPORT_EMAIL = 'support@jaz.com';

const formatExpirationDate = (isoDate, language) => {
  if (!isoDate) return null;
  const date = new Date(isoDate);
  if (isNaN(date.getTime())) return null;
  return date.toLocaleDateString(language === 'ar' ? 'ar-EG' : 'en-US', {
    day: '2-digit',
    month: 'long',
    year: 'numeric'
  });
};

const SubscriptionBlocked = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [language, setLanguage] = useState('ar');

  const status = searchParams.get('status') === 'suspended' ? 'suspended' : 'expired';
  const endDate = searchParams.get('end_date');
  const formattedDate = formatExpirationDate(endDate, language);

  const isSuspended = status === 'suspended';

  const copy = {
    ar: {
      expiredTitle: 'انتهى اشتراك شركتك',
      suspendedTitle: 'تم تعليق اشتراك شركتك',
      expiredDateLabel: 'انتهى الاشتراك في:',
      expiredExplanation: 'انتهى اشتراك شركتك ولم يعد لديك إمكانية الوصول إلى المنصة. يرجى تجديد الاشتراك لاستعادة الوصول.',
      suspendedExplanation: 'تم تعليق اشتراك شركتك مؤقتاً. يرجى التواصل مع دعم JAZ لمزيد من المعلومات.',
      whatsapp: 'تواصل عبر واتساب',
      email: 'البريد الإلكتروني للدعم',
      backToLogin: 'العودة لتسجيل الدخول'
    },
    en: {
      expiredTitle: 'Your Company Subscription Has Expired',
      suspendedTitle: 'Your Company Subscription Has Been Suspended',
      expiredDateLabel: 'Subscription expired on:',
      expiredExplanation: 'Your company subscription has expired and you no longer have access to the platform. Please renew your subscription to restore access.',
      suspendedExplanation: 'Your company subscription has been suspended. Please contact JAZ Support for more information.',
      whatsapp: 'Contact via WhatsApp',
      email: 'Support Email',
      backToLogin: 'Return to Login'
    }
  }[language];

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#F4F4F5] p-8" dir={language === 'ar' ? 'rtl' : 'ltr'}>
      <Card className="w-full max-w-md p-8 bg-white border border-gray-200 rounded-md shadow-sm text-center" data-testid="subscription-blocked-page">
        <div className="flex justify-end mb-4">
          <Button onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')} variant="ghost" size="sm" className="text-xs" data-testid="blocked-language-toggle">
            {language === 'ar' ? 'English' : 'العربية'}
          </Button>
        </div>

        <div className={`w-16 h-16 mx-auto mb-6 rounded-full flex items-center justify-center ${isSuspended ? 'bg-gray-100' : 'bg-red-50'}`}>
          {isSuspended ? (
            <Ban className="w-8 h-8 text-gray-500" data-testid="suspended-icon" />
          ) : (
            <AlertTriangle className="w-8 h-8 text-red-600" data-testid="expired-icon" />
          )}
        </div>

        <h1 className="text-2xl font-bold text-[#0A0A0A] mb-4" data-testid="blocked-title">
          {isSuspended ? copy.suspendedTitle : copy.expiredTitle}
        </h1>

        {!isSuspended && formattedDate && (
          <div className="mb-4" data-testid="blocked-expiration-date">
            <p className="text-sm text-gray-500">{copy.expiredDateLabel}</p>
            <p className="text-lg font-semibold text-[#0A0A0A]">{formattedDate}</p>
          </div>
        )}

        <p className="text-sm text-gray-600 mb-8">
          {isSuspended ? copy.suspendedExplanation : copy.expiredExplanation}
        </p>

        <div className="space-y-3">
          <a href={WHATSAPP_SUPPORT_URL} target="_blank" rel="noopener noreferrer" data-testid="blocked-whatsapp-btn">
            <Button type="button" className="w-full bg-[#25D366] hover:bg-[#1ebe57] text-white rounded-sm">
              <MessageCircle className="w-4 h-4 me-2" /> {copy.whatsapp}
            </Button>
          </a>
          <a href={`mailto:${SUPPORT_EMAIL}`} data-testid="blocked-email-btn">
            <Button type="button" variant="outline" className="w-full rounded-sm">
              <Mail className="w-4 h-4 me-2" /> {SUPPORT_EMAIL}
            </Button>
          </a>
          <Button type="button" variant="ghost" className="w-full rounded-sm" onClick={() => navigate('/')} data-testid="blocked-return-login-btn">
            <LogIn className="w-4 h-4 me-2" /> {copy.backToLogin}
          </Button>
        </div>
      </Card>
    </div>
  );
};

export default SubscriptionBlocked;
