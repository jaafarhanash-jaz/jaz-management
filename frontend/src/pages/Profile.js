import { useEffect, useRef, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import api from '@/utils/api';
import { t } from '@/utils/translations';
import {
  User, Mail, Briefcase, Camera, Trash2, Upload,
  KeyRound, Globe, ShieldCheck, Loader2,
} from 'lucide-react';
import { toast } from 'sonner';

const ROLE_LABELS = {
  super_admin: { ar: 'مدير النظام', en: 'Super Admin' },
  company_owner: { ar: 'صاحب الشركة', en: 'Company Owner' },
  employee: { ar: 'موظف', en: 'Employee' },
};
const STATUS_LABELS = {
  active: { ar: 'نشط', en: 'Active' },
  inactive: { ar: 'غير نشط', en: 'Inactive' },
};

const ALLOWED_PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

const SectionCard = ({ icon: Icon, title, subtitle, children, testId }) => (
  <Card className="bg-white border border-gray-200 rounded-md shadow-sm p-6" data-testid={testId}>
    <div className="flex items-center gap-2 mb-1">
      <Icon className="w-5 h-5 text-[#0033A0]" />
      <h2 className="text-lg font-bold text-[#0A0A0A]">{title}</h2>
    </div>
    {subtitle && <p className="text-sm text-gray-500 mb-4">{subtitle}</p>}
    {!subtitle && <div className="mb-2" />}
    {children}
  </Card>
);

const ReadOnlyField = ({ label, value }) => (
  <div>
    <Label className="text-gray-500">{label}</Label>
    <p className="mt-1.5 px-3 py-2 bg-gray-50 border border-gray-100 rounded-sm text-sm text-gray-700" data-testid={`readonly-${label}`}>
      {value || '-'}
    </p>
  </div>
);

const initials = (name) => (name || '').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

// Reused by every role (Super Admin, Company Owner, Employee) - same page,
// same components, only role-specific read-only fields (role label,
// department/position when present) differ. GET /auth/me and
// PUT /employee/profile already work for any authenticated role (a
// pre-existing, documented gap - see server.py); the new /profile/photo and
// /profile/password endpoints are role-neutral by design, so this is one
// implementation, not three.
const Profile = ({ onLogout, language, setLanguage, userRole }) => {
  const isAr = language === 'ar';
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState({ name: '', phone: '' });
  const [savingInfo, setSavingInfo] = useState(false);

  const [photoDataUrl, setPhotoDataUrl] = useState(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const fileInputRef = useRef(null);

  const [pwForm, setPwForm] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [changingPassword, setChangingPassword] = useState(false);

  const fetchProfile = async () => {
    setLoading(true);
    try {
      const res = await api.get('/auth/me');
      setProfile(res.data);
      setForm({ name: res.data.name || '', phone: res.data.phone || '' });
      if (res.data.photo) fetchPhotoPreview();
      else setPhotoDataUrl(null);
    } catch (e) {
      toast.error(isAr ? 'تعذر تحميل بيانات الملف الشخصي' : 'Failed to load profile data');
    }
    setLoading(false);
  };

  const fetchPhotoPreview = async () => {
    try {
      const res = await api.get('/profile/photo');
      setPhotoDataUrl(res.data.data);
    } catch (e) {
      setPhotoDataUrl(null);
    }
  };

  useEffect(() => {
    fetchProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSaveInfo = async (e) => {
    e.preventDefault();
    if (!form.name.trim() || !form.phone.trim()) {
      toast.error(isAr ? 'الاسم ورقم الهاتف مطلوبان' : 'Name and phone are required');
      return;
    }
    setSavingInfo(true);
    try {
      await api.put('/employee/profile', { name: form.name.trim(), phone: form.phone.trim() });
      toast.success(isAr ? 'تم تحديث المعلومات بنجاح' : 'Information updated successfully');
      fetchProfile();
    } catch (err) {
      toast.error(err.response?.data?.detail || (isAr ? 'حدث خطأ' : 'Something went wrong'));
    }
    setSavingInfo(false);
  };

  const handlePhotoSelect = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!ALLOWED_PHOTO_TYPES.includes(file.type)) {
      toast.error(isAr ? 'يجب أن تكون الصورة بصيغة JPG أو PNG أو WEBP أو GIF' : 'Photo must be a JPG, PNG, WEBP, or GIF image');
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      toast.error(isAr ? 'حجم الصورة يجب ألا يتجاوز 5 ميجابايت' : 'Photo must not exceed 5MB');
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      setPhotoBusy(true);
      try {
        await api.post('/profile/photo', { filename: file.name, mime_type: file.type, data: reader.result });
        toast.success(isAr ? 'تم تحديث الصورة الشخصية' : 'Profile photo updated');
        await fetchProfile();
      } catch (err) {
        toast.error(err.response?.data?.detail || (isAr ? 'حدث خطأ' : 'Something went wrong'));
      }
      setPhotoBusy(false);
    };
    reader.readAsDataURL(file);
  };

  const handleRemovePhoto = async () => {
    setPhotoBusy(true);
    try {
      await api.delete('/profile/photo');
      toast.success(isAr ? 'تم حذف الصورة الشخصية' : 'Profile photo removed');
      setPhotoDataUrl(null);
      fetchProfile();
    } catch (err) {
      toast.error(err.response?.data?.detail || (isAr ? 'حدث خطأ' : 'Something went wrong'));
    }
    setPhotoBusy(false);
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    if (!pwForm.current_password || !pwForm.new_password || !pwForm.confirm_password) {
      toast.error(isAr ? 'يرجى تعبئة جميع الحقول' : 'Please fill in all fields');
      return;
    }
    if (pwForm.new_password.length < 6) {
      toast.error(isAr ? 'كلمة المرور الجديدة يجب ألا تقل عن 6 أحرف' : 'New password must be at least 6 characters');
      return;
    }
    if (pwForm.new_password !== pwForm.confirm_password) {
      toast.error(isAr ? 'كلمتا المرور غير متطابقتين' : 'Passwords do not match');
      return;
    }
    setChangingPassword(true);
    try {
      await api.put('/profile/password', {
        current_password: pwForm.current_password,
        new_password: pwForm.new_password,
      });
      toast.success(isAr ? 'تم تغيير كلمة المرور بنجاح' : 'Password changed successfully');
      setPwForm({ current_password: '', new_password: '', confirm_password: '' });
    } catch (err) {
      toast.error(err.response?.data?.detail || (isAr ? 'حدث خطأ' : 'Something went wrong'));
    }
    setChangingPassword(false);
  };

  const roleLabel = profile ? (ROLE_LABELS[profile.role]?.[language] || profile.role) : '';
  const statusLabel = profile ? (STATUS_LABELS[profile.status]?.[language] || profile.status) : '';

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div>
          <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="profile-title">
            {t('profile', language)}
          </h1>
          <p className="text-gray-600 mt-2">
            {isAr ? 'إدارة معلوماتك الشخصية وإعدادات الحساب' : 'Manage your personal information and account settings'}
          </p>
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">Loading...</div>
        ) : !profile ? (
          <div className="text-center py-12 text-gray-500" data-testid="profile-error">
            <p className="mb-4">{isAr ? 'تعذر تحميل بيانات الملف الشخصي' : 'Failed to load profile data'}</p>
            <Button onClick={fetchProfile} className="bg-[#0033A0] hover:bg-[#002277] text-white rounded-sm" data-testid="profile-retry-btn">
              {isAr ? 'إعادة المحاولة' : 'Retry'}
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Profile Photo */}
            <div className="lg:col-span-1">
              <SectionCard icon={Camera} title={isAr ? 'الصورة الشخصية' : 'Profile Photo'} testId="section-photo">
                <div className="flex flex-col items-center gap-4">
                  <div
                    className="w-28 h-28 rounded-full overflow-hidden bg-[#0033A0] flex items-center justify-center text-white text-3xl font-bold border-4 border-gray-100 shadow-sm transition-transform duration-200 hover:scale-[1.03]"
                    data-testid="profile-photo-preview"
                  >
                    {photoDataUrl ? (
                      <img src={photoDataUrl} alt={profile.name} className="w-full h-full object-cover" />
                    ) : (
                      <span>{initials(profile.name)}</span>
                    )}
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/gif"
                    className="hidden"
                    onChange={handlePhotoSelect}
                    data-testid="photo-file-input"
                  />
                  <div className="flex flex-wrap justify-center gap-2 w-full">
                    <Button
                      type="button" variant="outline" size="sm" disabled={photoBusy}
                      onClick={() => fileInputRef.current?.click()}
                      data-testid="photo-upload-btn"
                    >
                      {photoBusy ? <Loader2 className="w-3.5 h-3.5 me-1.5 animate-spin" /> : <Upload className="w-3.5 h-3.5 me-1.5" />}
                      {photoDataUrl ? (isAr ? 'استبدال' : 'Replace') : (isAr ? 'رفع صورة' : 'Upload Photo')}
                    </Button>
                    {photoDataUrl && (
                      <Button
                        type="button" variant="outline" size="sm" disabled={photoBusy}
                        onClick={handleRemovePhoto}
                        className="text-red-600 hover:bg-red-50 hover:text-red-700"
                        data-testid="photo-remove-btn"
                      >
                        <Trash2 className="w-3.5 h-3.5 me-1.5" /> {isAr ? 'إزالة' : 'Remove'}
                      </Button>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 text-center">
                    {isAr ? 'JPG أو PNG أو WEBP أو GIF - بحد أقصى 5 ميجابايت' : 'JPG, PNG, WEBP or GIF - max 5MB'}
                  </p>
                </div>
              </SectionCard>
            </div>

            {/* Personal Information */}
            <div className="lg:col-span-2 space-y-6">
              <SectionCard icon={User} title={isAr ? 'المعلومات الشخصية' : 'Personal Information'} testId="section-personal-info">
                <form onSubmit={handleSaveInfo} className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <Label>{isAr ? 'الاسم الكامل' : 'Full Name'}</Label>
                      <Input
                        data-testid="profile-name-input" required
                        value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                      />
                    </div>
                    <div>
                      <Label>{isAr ? 'رقم الهاتف' : 'Phone Number'}</Label>
                      <Input
                        data-testid="profile-phone-input" required
                        value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })}
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <ReadOnlyField label={isAr ? 'البريد الإلكتروني' : 'Email Address'} value={profile.email} />
                    {profile.position && <ReadOnlyField label={isAr ? 'المسمى الوظيفي' : 'Job Title'} value={profile.position} />}
                    {profile.department && <ReadOnlyField label={isAr ? 'القسم' : 'Department'} value={profile.department} />}
                  </div>
                  <p className="text-xs text-gray-400 flex items-center gap-1">
                    <Mail className="w-3 h-3" /> {isAr ? 'لا يمكن تغيير البريد الإلكتروني من هنا' : 'Email address cannot be changed from here'}
                  </p>
                  <div className="flex justify-end pt-2 border-t border-gray-100">
                    <Button type="submit" disabled={savingInfo} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="save-profile-btn">
                      {savingInfo ? (isAr ? 'جارِ الحفظ...' : 'Saving...') : (isAr ? 'حفظ التغييرات' : 'Save Changes')}
                    </Button>
                  </div>
                </form>
              </SectionCard>

              {/* Security */}
              <SectionCard icon={KeyRound} title={isAr ? 'الأمان' : 'Security'} subtitle={isAr ? 'تغيير كلمة المرور الخاصة بحسابك' : 'Change your account password'} testId="section-security">
                <form onSubmit={handleChangePassword} className="space-y-4">
                  <div>
                    <Label>{isAr ? 'كلمة المرور الحالية' : 'Current Password'}</Label>
                    <Input
                      type="password" required data-testid="current-password-input"
                      value={pwForm.current_password}
                      onChange={(e) => setPwForm({ ...pwForm, current_password: e.target.value })}
                      autoComplete="current-password"
                    />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <Label>{isAr ? 'كلمة المرور الجديدة' : 'New Password'}</Label>
                      <Input
                        type="password" required data-testid="new-password-input"
                        value={pwForm.new_password}
                        onChange={(e) => setPwForm({ ...pwForm, new_password: e.target.value })}
                        autoComplete="new-password"
                      />
                    </div>
                    <div>
                      <Label>{isAr ? 'تأكيد كلمة المرور الجديدة' : 'Confirm New Password'}</Label>
                      <Input
                        type="password" required data-testid="confirm-password-input"
                        value={pwForm.confirm_password}
                        onChange={(e) => setPwForm({ ...pwForm, confirm_password: e.target.value })}
                        autoComplete="new-password"
                      />
                    </div>
                  </div>
                  <p className="text-xs text-gray-400">
                    {isAr ? 'يجب ألا تقل كلمة المرور الجديدة عن 6 أحرف' : 'New password must be at least 6 characters'}
                  </p>
                  <div className="flex justify-end pt-2 border-t border-gray-100">
                    <Button type="submit" disabled={changingPassword} className="bg-[#0033A0] hover:bg-[#002277]" data-testid="change-password-btn">
                      {changingPassword ? (isAr ? 'جارِ التغيير...' : 'Changing...') : (isAr ? 'تغيير كلمة المرور' : 'Change Password')}
                    </Button>
                  </div>
                </form>
              </SectionCard>

              {/* Account Settings */}
              <SectionCard icon={ShieldCheck} title={isAr ? 'إعدادات الحساب' : 'Account Settings'} testId="section-account-settings">
                <div className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <ReadOnlyField label={isAr ? 'نوع الحساب' : 'Account Role'} value={roleLabel} />
                    <ReadOnlyField label={isAr ? 'حالة الحساب' : 'Account Status'} value={statusLabel} />
                  </div>
                  <div>
                    <Label className="flex items-center gap-1.5"><Globe className="w-3.5 h-3.5 text-gray-500" /> {isAr ? 'اللغة المفضلة' : 'Preferred Language'}</Label>
                    <div className="mt-1.5 inline-flex items-center gap-0.5 bg-gray-100 rounded-sm p-0.5" data-testid="language-preference-group">
                      <button
                        type="button"
                        onClick={() => setLanguage('ar')}
                        data-testid="language-pref-ar"
                        className={`px-4 py-1.5 text-sm font-medium rounded-sm transition-colors ${language === 'ar' ? 'bg-[#0033A0] text-white' : 'text-gray-600 hover:bg-gray-200'}`}
                      >
                        العربية
                      </button>
                      <button
                        type="button"
                        onClick={() => setLanguage('en')}
                        data-testid="language-pref-en"
                        className={`px-4 py-1.5 text-sm font-medium rounded-sm transition-colors ${language === 'en' ? 'bg-[#0033A0] text-white' : 'text-gray-600 hover:bg-gray-200'}`}
                      >
                        English
                      </button>
                    </div>
                  </div>
                </div>
              </SectionCard>

              <div className="flex items-start gap-2 text-xs text-gray-400 px-1">
                <Briefcase className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <p>
                  {isAr
                    ? 'يمكنك فقط عرض وتعديل معلومات حسابك الخاص. لا يمكن الوصول إلى حسابات المستخدمين الآخرين من هذه الصفحة.'
                    : 'You may only view and edit your own account information. Other users\' accounts cannot be accessed from this page.'}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
};

export default Profile;
