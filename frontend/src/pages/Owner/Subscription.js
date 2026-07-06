import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/utils/api';
import { CreditCard, Check, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

const OwnerSubscription = ({ onLogout, language, setLanguage }) => {
  const [plans, setPlans] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [processingPlan, setProcessingPlan] = useState(null);
  const [pollingStatus, setPollingStatus] = useState('');
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    fetchAll();
    const params = new URLSearchParams(location.search);
    const sessionId = params.get('session_id');
    if (sessionId) {
      pollPayment(sessionId, 0);
    }
  }, []);

  const fetchAll = async () => {
    try {
      const [plansRes, currentRes] = await Promise.all([
        api.get('/owner/subscription-plans'),
        api.get('/owner/subscription')
      ]);
      setPlans(plansRes.data);
      setCurrent(currentRes.data);
    } catch (e) { toast.error('خطأ'); }
    setLoading(false);
  };

  const pollPayment = async (sessionId, attempts) => {
    const maxAttempts = 10;
    if (attempts >= maxAttempts) {
      setPollingStatus('انتهى وقت التحقق');
      toast.error('انتهى وقت التحقق من الدفع');
      navigate('/company-owner/subscription', { replace: true });
      return;
    }
    setPollingStatus('جاري التحقق من الدفع...');
    try {
      const res = await api.get(`/payments/status/${sessionId}`);
      if (res.data.payment_status === 'paid') {
        setPollingStatus('');
        toast.success('تم الاشتراك بنجاح!');
        navigate('/company-owner/subscription', { replace: true });
        fetchAll();
        return;
      } else if (res.data.status === 'expired') {
        setPollingStatus('');
        toast.error('انتهت جلسة الدفع');
        navigate('/company-owner/subscription', { replace: true });
        return;
      }
      setTimeout(() => pollPayment(sessionId, attempts + 1), 2000);
    } catch (e) {
      setTimeout(() => pollPayment(sessionId, attempts + 1), 2000);
    }
  };

  const subscribe = async (planId) => {
    setProcessingPlan(planId);
    try {
      const res = await api.post('/payments/checkout', {
        plan_id: planId,
        origin_url: window.location.origin
      });
      window.location.href = res.data.url;
    } catch (e) {
      toast.error(e.response?.data?.detail || 'حدث خطأ في الدفع');
      setProcessingPlan(null);
    }
  };

  return (
    <Layout userRole="company_owner" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="subscription-title">الاشتراك</h1>
          <p className="text-gray-600 mt-2">إدارة اشتراك شركتك</p>
        </div>

        {pollingStatus && (
          <Card className="p-4 bg-yellow-50 border border-yellow-200 flex items-center gap-3">
            <Loader2 className="w-5 h-5 animate-spin text-yellow-700" />
            <p className="text-sm text-yellow-800">{pollingStatus}</p>
          </Card>
        )}

        {/* Current Subscription */}
        {current && current.current_plan && (
          <Card className="p-6 bg-white border border-gray-200 rounded-md shadow-sm" data-testid="current-subscription">
            <div className="flex items-start justify-between flex-wrap gap-4">
              <div>
                <p className="text-xs uppercase tracking-wider text-gray-500 mb-1">الاشتراك الحالي</p>
                <h2 className="text-2xl font-bold text-[#0A0A0A]">{current.current_plan.name}</h2>
                <p className="text-sm text-gray-600 mt-1">
                  ينتهي في: {current.subscription_end_date ? new Date(current.subscription_end_date).toLocaleDateString('ar-EG') : '-'}
                </p>
              </div>
              <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                current.subscription_status === 'active' ? 'bg-green-50 text-green-700 border-green-200' :
                'bg-gray-50 text-gray-700 border-gray-200'
              }`}>
                {current.subscription_status === 'active' ? 'نشط' : 'موقوف'}
              </span>
            </div>
          </Card>
        )}

        {/* Available Plans */}
        <div>
          <h2 className="text-2xl font-bold text-[#0A0A0A] mb-4">الخطط المتاحة</h2>
          {loading ? (
            <div className="text-center py-12">Loading...</div>
          ) : plans.length === 0 ? (
            <Card className="p-12 text-center bg-white border border-gray-200">
              <CreditCard className="w-12 h-12 mx-auto text-gray-300 mb-4" />
              <p className="text-gray-500">لا توجد خطط متاحة</p>
            </Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {plans.map((p) => (
                <Card key={p.id} className="p-6 bg-white border border-gray-200 rounded-md stat-card" data-testid={`plan-${p.id}`}>
                  <h3 className="text-xl font-bold text-[#0A0A0A]">{p.name}</h3>
                  <p className="text-4xl font-bold text-[#0033A0] mt-3">${p.price}
                    <span className="text-sm text-gray-500 font-normal">/{p.duration_months} شهر</span>
                  </p>
                  <div className="mt-4 space-y-2 text-sm">
                    <div className="flex items-center gap-2 text-gray-700"><Check className="w-4 h-4 text-green-600" />حتى {p.max_employees} موظف</div>
                    {p.features && p.features.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 text-gray-700"><Check className="w-4 h-4 text-green-600" />{f}</div>
                    ))}
                  </div>
                  <Button
                    onClick={() => subscribe(p.id)}
                    disabled={processingPlan === p.id}
                    className="w-full mt-6 bg-[#0033A0] hover:bg-[#002277]"
                    data-testid={`subscribe-btn-${p.id}`}
                  >
                    {processingPlan === p.id ? <Loader2 className="w-4 h-4 animate-spin" /> : 'اشتراك الآن'}
                  </Button>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
};

export default OwnerSubscription;
