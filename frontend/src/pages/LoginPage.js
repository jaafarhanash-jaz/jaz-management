import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card } from '@/components/ui/card';
import api from '@/utils/api';
import { toast } from 'sonner';
import { t } from '@/utils/translations';

const LoginPage = ({ onLogin }) => {
  const navigate = useNavigate();
  const [credentials, setCredentials] = useState({ email_or_phone: '', password: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [language, setLanguage] = useState('ar');

  useEffect(() => {
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
    document.documentElement.lang = language;
  }, [language]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const response = await api.post('/auth/login', credentials);
      toast.success(t('successLogin', language));
      onLogin(response.data.token, response.data.refresh_token, response.data.role);
    } catch (err) {
      const detail = err.response?.data?.detail;
      const status = err.response?.status;
      if (status === 403 && detail && typeof detail === 'object' && detail.field === 'subscription') {
        const params = new URLSearchParams({
          status: detail.status || '',
          end_date: detail.subscription_end_date || ''
        });
        navigate(`/subscription-blocked?${params.toString()}`);
        return;
      }
      // Only a genuine 401 means the credentials are wrong. Anything else
      // (503 DB-unavailable, 500, or no response at all = network/timeout)
      // must NOT be reported as "invalid password" - that masked the real
      // cause. Surface the actual condition so it can be acted on.
      let errorMsg;
      if (status === 401) {
        errorMsg = language === 'ar' ? 'البريد الإلكتروني أو كلمة المرور غير صحيحة' : 'Invalid email or password';
      } else if (!err.response) {
        errorMsg = language === 'ar'
          ? 'تعذر الوصول إلى الخادم. تحقق من الاتصال وحاول مرة أخرى.'
          : 'Cannot reach the server. Check your connection and try again.';
      } else if (typeof detail === 'string' && detail) {
        // Backend-provided message (e.g. the 503 "database unavailable").
        errorMsg = detail;
      } else {
        errorMsg = language === 'ar'
          ? `تعذر تسجيل الدخول (خطأ ${status}). يرجى المحاولة لاحقاً.`
          : `Sign-in failed (error ${status}). Please try again later.`;
      }
      setError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex" dir={language === 'ar' ? 'rtl' : 'ltr'}>
      {/* Left Side - Image */}
      <div className="hidden lg:flex lg:w-1/2 relative">
        <img
          src="https://images.unsplash.com/photo-1488972685288-c3fd157d7c7a?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1Mjh8MHwxfHNlYXJjaHwxfHxhYnN0cmFjdCUyMG9mZmljZSUyMGdlb21ldHJpY3xlbnwwfHx8fDE3ODI5Mjk0MDl8MA&ixlib=rb-4.1.0&q=85"
          alt="JAZ Platform"
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-[#0033A0]/80 flex items-center justify-center">
          <div className="text-center text-white px-8">
            <h1 className="text-5xl font-bold mb-4">JAZ</h1>
            <p className="text-xl">منصة إدارة الموظفين والشركات</p>
          </div>
        </div>
      </div>

      {/* Right Side - Login Form */}
      <div className="w-full lg:w-1/2 flex items-center justify-center p-8 bg-[#F4F4F5]">
        <Card className="w-full max-w-md p-8 bg-white border border-gray-200 rounded-md shadow-sm">
          <div className="mb-8">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-3xl font-bold text-[#0033A0]" data-testid="login-title">
                {t('welcome', language)}
              </h2>
              <Button
                onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}
                variant="ghost"
                size="sm"
                className="text-xs"
                data-testid="language-toggle"
              >
                {language === 'ar' ? 'English' : 'العربية'}
              </Button>
            </div>
            <p className="text-gray-600">{t('loginSubtitle', language)}</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            {error && (
              <div
                data-testid="login-error-message"
                className="p-3 bg-red-50 border border-red-200 rounded-sm text-sm text-red-700"
              >
                {error}
              </div>
            )}
            <div>
              <Label htmlFor="email_or_phone" className="text-sm font-medium text-gray-700">
                {t('emailOrPhone', language)}
              </Label>
              <Input
                id="email_or_phone"
                data-testid="login-email-input"
                type="text"
                value={credentials.email_or_phone}
                onChange={(e) => setCredentials({ ...credentials, email_or_phone: e.target.value })}
                className="mt-1 bg-white border border-gray-300 rounded-sm px-3 py-2 focus:ring-2 focus:ring-[#0033A0]/20 focus:border-[#0033A0]"
                required
              />
            </div>

            <div>
              <Label htmlFor="password" className="text-sm font-medium text-gray-700">
                {t('password', language)}
              </Label>
              <Input
                id="password"
                data-testid="login-password-input"
                type="password"
                value={credentials.password}
                onChange={(e) => setCredentials({ ...credentials, password: e.target.value })}
                className="mt-1 bg-white border border-gray-300 rounded-sm px-3 py-2 focus:ring-2 focus:ring-[#0033A0]/20 focus:border-[#0033A0]"
                required
              />
            </div>

            <Button
              type="submit"
              data-testid="login-submit-btn"
              disabled={loading}
              className="w-full bg-[#0033A0] text-white hover:bg-[#002277] transition-colors rounded-sm px-4 py-2 font-medium shadow-sm"
            >
              {loading ? 'جاري التسجيل...' : t('login', language)}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
};

export default LoginPage;