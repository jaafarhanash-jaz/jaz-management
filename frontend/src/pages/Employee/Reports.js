import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import { Upload, X, Send, Image as ImageIcon } from 'lucide-react';
import { toast } from 'sonner';

const EmployeeReports = ({ onLogout, language, setLanguage }) => {
  const [form, setForm] = useState({ title: '', description: '' });
  const [images, setImages] = useState([]);
  const [files, setFiles] = useState([]);
  const [submitting, setSubmitting] = useState(false);

  const readAsBase64 = (file) => new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(file);
  });

  const handleImageUpload = async (e) => {
    const list = Array.from(e.target.files || []);
    const converted = await Promise.all(list.map(readAsBase64));
    setImages([...images, ...converted]);
  };

  const handleFileUpload = async (e) => {
    const list = Array.from(e.target.files || []);
    const converted = await Promise.all(list.map(readAsBase64));
    setFiles([...files, ...converted]);
  };

  const removeImage = (idx) => setImages(images.filter((_, i) => i !== idx));
  const removeFile = (idx) => setFiles(files.filter((_, i) => i !== idx));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post('/employee/reports', { ...form, images, files });
      toast.success('تم إرسال التقرير بنجاح');
      setForm({ title: '', description: '' });
      setImages([]);
      setFiles([]);
    } catch (err) { toast.error('حدث خطأ'); }
    setSubmitting(false);
  };

  return (
    <Layout userRole="employee" onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="employee-reports-title">
            {t('reports', language)}
          </h1>
          <p className="text-gray-600 mt-2">إرسال تقرير جديد</p>
        </div>

        <Card className="p-8 bg-white border border-gray-200 rounded-md shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <Label>عنوان التقرير</Label>
              <Input data-testid="report-title-input" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div>
              <Label>الوصف</Label>
              <Textarea data-testid="report-description-input" required rows={6} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>

            {/* Images */}
            <div>
              <Label>الصور (اختياري)</Label>
              <div className="mt-2">
                <label className="cursor-pointer">
                  <input type="file" multiple accept="image/*" className="hidden" onChange={handleImageUpload} data-testid="report-images-input" />
                  <div className="border-2 border-dashed border-gray-300 rounded-sm p-6 text-center hover:border-[#0033A0] transition-colors">
                    <ImageIcon className="w-8 h-8 mx-auto text-gray-400 mb-2" />
                    <p className="text-sm text-gray-500">اضغط لإضافة صور</p>
                  </div>
                </label>
              </div>
              {images.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {images.map((img, i) => (
                    <div key={i} className="relative">
                      <img src={img} alt="preview" className="w-20 h-20 object-cover rounded-sm border border-gray-200" />
                      <button type="button" onClick={() => removeImage(i)} className="absolute -top-1 -end-1 bg-red-600 text-white rounded-full w-5 h-5 flex items-center justify-center" data-testid={`remove-image-${i}`}>
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Files */}
            <div>
              <Label>الملفات (اختياري)</Label>
              <div className="mt-2">
                <label className="cursor-pointer">
                  <input type="file" multiple accept=".pdf,.doc,.docx,.xls,.xlsx" className="hidden" onChange={handleFileUpload} data-testid="report-files-input" />
                  <div className="border-2 border-dashed border-gray-300 rounded-sm p-6 text-center hover:border-[#0033A0] transition-colors">
                    <Upload className="w-8 h-8 mx-auto text-gray-400 mb-2" />
                    <p className="text-sm text-gray-500">اضغط لإضافة ملفات</p>
                  </div>
                </label>
              </div>
              {files.length > 0 && (
                <div className="mt-3 space-y-1">
                  {files.map((_, i) => (
                    <div key={i} className="flex items-center justify-between bg-gray-50 px-3 py-2 rounded-sm border border-gray-200">
                      <span className="text-xs text-gray-600">ملف {i + 1}</span>
                      <button type="button" onClick={() => removeFile(i)} className="text-red-600 hover:text-red-700" data-testid={`remove-file-${i}`}>
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <Button type="submit" disabled={submitting} className="bg-[#0033A0] hover:bg-[#002277] w-full" data-testid="submit-report-btn">
              <Send className="w-4 h-4 me-2" /> {submitting ? 'جاري الإرسال...' : 'إرسال التقرير'}
            </Button>
          </form>
        </Card>
      </div>
    </Layout>
  );
};

export default EmployeeReports;
