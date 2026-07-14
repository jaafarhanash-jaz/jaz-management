import { useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import { TrendingUp, Award, CheckCircle, Clock } from 'lucide-react';
import { toast } from 'sonner';

const COLORS = ['#00A36C', '#FFB800', '#E11D48'];

const EmployeePerformance = ({ onLogout, language, setLanguage }) => {
  const [perf, setPerf] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchPerf(); }, []);

  const fetchPerf = async () => {
    setLoading(true);
    try {
      const res = await api.get('/employee/performance');
      setPerf(res.data);
    } catch (e) { toast.error('خطأ'); }
    setLoading(false);
  };

  if (loading) return <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}><div className="text-center py-12">Loading...</div></Layout>;

  // The fetch can fail (stale token / network / backend restart), leaving
  // perf null while loading is false. Without this guard the tasksData/render
  // below would dereference null and crash the page (same class as the Owner
  // Dashboard bug). Render an explicit, recoverable state instead.
  if (!perf) return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="text-center py-12 text-gray-500" data-testid="employee-performance-error">
        <p className="mb-4">تعذر تحميل بيانات الأداء. تحقق من الاتصال وحاول مرة أخرى.</p>
        <button onClick={fetchPerf} className="px-4 py-2 rounded-sm bg-[#0033A0] hover:bg-[#002277] text-white" data-testid="performance-retry-btn">
          إعادة المحاولة
        </button>
      </div>
    </Layout>
  );

  const tasksData = [
    { name: 'مكتملة', value: perf.completed_tasks, color: '#00A36C' },
    { name: 'متأخرة', value: perf.overdue_tasks, color: '#E11D48' },
    { name: 'أخرى', value: perf.total_tasks - perf.completed_tasks - perf.overdue_tasks, color: '#FFB800' },
  ].filter(d => d.value > 0);

  const attendanceData = [
    { name: 'حاضر', value: perf.present_days },
    { name: 'متأخر', value: perf.late_days },
    { name: 'غائب', value: perf.absent_days },
  ];

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-performance-title">
            {t('performance', language)}
          </h1>
          <p className="text-gray-600 mt-2">تقييم أدائي</p>
        </div>

        {/* Overall Score */}
        <Card className="p-8 bg-white border border-gray-200 rounded-md shadow-sm">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <div className="flex items-center gap-2 text-gray-500 mb-2">
                <Award className="w-5 h-5" />
                <span className="text-xs uppercase tracking-wider">التقييم العام</span>
              </div>
              <p className="text-5xl font-bold text-[#0033A0]" data-testid="performance-score">{perf.performance_score}%</p>
            </div>
            <div className="w-64">
              <Progress value={perf.performance_score} className="h-3" />
            </div>
          </div>
        </Card>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="p-6 bg-white border border-gray-200 rounded-md stat-card" data-testid="stat-completion">
            <div className="flex items-center gap-2 text-gray-500 mb-2">
              <CheckCircle className="w-4 h-4" /><span className="text-xs uppercase">إنجاز المهام</span>
            </div>
            <p className="text-3xl font-bold text-[#0A0A0A]">{perf.completion_rate}%</p>
            <p className="text-xs text-gray-500 mt-1">{perf.completed_tasks} من {perf.total_tasks}</p>
          </Card>
          <Card className="p-6 bg-white border border-gray-200 rounded-md stat-card" data-testid="stat-attendance">
            <div className="flex items-center gap-2 text-gray-500 mb-2">
              <TrendingUp className="w-4 h-4" /><span className="text-xs uppercase">نسبة الالتزام</span>
            </div>
            <p className="text-3xl font-bold text-[#0A0A0A]">{perf.attendance_rate}%</p>
            <p className="text-xs text-gray-500 mt-1">{perf.present_days} يوم حضور</p>
          </Card>
          <Card className="p-6 bg-white border border-gray-200 rounded-md stat-card">
            <div className="flex items-center gap-2 text-gray-500 mb-2">
              <Clock className="w-4 h-4" /><span className="text-xs uppercase">التأخيرات</span>
            </div>
            <p className="text-3xl font-bold text-[#0A0A0A]">{perf.late_days}</p>
            <p className="text-xs text-gray-500 mt-1">مرة</p>
          </Card>
          <Card className="p-6 bg-white border border-gray-200 rounded-md stat-card">
            <div className="flex items-center gap-2 text-gray-500 mb-2">
              <Clock className="w-4 h-4" /><span className="text-xs uppercase">الغيابات</span>
            </div>
            <p className="text-3xl font-bold text-[#0A0A0A]">{perf.absent_days}</p>
            <p className="text-xs text-gray-500 mt-1">يوم</p>
          </Card>
        </div>

        {/* Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card className="p-6 bg-white border border-gray-200 rounded-md">
            <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">توزيع المهام</h3>
            {tasksData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie data={tasksData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={100} label>
                    {tasksData.map((entry, index) => <Cell key={index} fill={entry.color} />)}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : <p className="text-center text-gray-500 py-12">لا توجد بيانات</p>}
          </Card>
          <Card className="p-6 bg-white border border-gray-200 rounded-md">
            <h3 className="text-lg font-medium text-[#0A0A0A] mb-4">سجل الحضور</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={attendanceData}>
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" fill="#0033A0" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </div>
    </Layout>
  );
};

export default EmployeePerformance;
