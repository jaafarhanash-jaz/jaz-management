import { useCallback, useEffect, useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import api from '@/utils/api';
import { validateAndFocus } from '@/utils/formValidation';
import { ts, roleName } from '@/utils/salesTranslations';
import { useSalesAccess } from '@/components/sales/SalesAccess';
import SalesNoAccess from '@/components/sales/SalesNoAccess';
import { toast } from 'sonner';
import { KeyRound, Pencil, Plus, Power, ShieldCheck, Users } from 'lucide-react';

// Which server permission each action needs. The buttons below are only
// rendered for users holding them - but that is a courtesy: the API enforces
// the same permissions on every call, whatever the UI shows.
const P = { create: 'sales.staff.create', update: 'sales.staff.update', assign: 'sales.staff.assign_roles' };

const EMPTY_CREATE = { name: '', email: '', phone: '', password: '', role_keys: [] };

const SalesTeam = ({ onLogout, language, setLanguage, userRole }) => {
  const { can, hasModule, loading: accessLoading } = useSalesAccess();

  const [team, setTeam] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_CREATE);
  const [createErrors, setCreateErrors] = useState({});
  const [editing, setEditing] = useState(null);        // staff row being edited
  const [editForm, setEditForm] = useState({ name: '', email: '', phone: '' });
  const [editErrors, setEditErrors] = useState({});
  const [rolesTargetId, setRolesTargetId] = useState(null);
  const [busyRole, setBusyRole] = useState(null);
  const [resetting, setResetting] = useState(null);
  const [newPassword, setNewPassword] = useState('');
  const [resetErrors, setResetErrors] = useState({});

  const canManage = can(P.update) || can(P.assign);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [teamRes, rolesRes] = await Promise.all([api.get('/sales/team?limit=500'), api.get('/sales/roles')]);
      setTeam(teamRes.data.items);
      setRoles(rolesRes.data);
    } catch (e) {
      toast.error(ts('toast_error', language));
    }
    setLoading(false);
  }, [language]);

  useEffect(() => { if (hasModule('team')) fetchAll(); }, [hasModule, fetchAll]);

  const showServerError = (err, setFieldErrors) => {
    const detail = err.response?.data?.detail;
    if (detail && typeof detail === 'object' && detail.field) {
      setFieldErrors && setFieldErrors({ [detail.field]: detail.message });
      toast.error(detail.message);
    } else if (typeof detail === 'string') {
      toast.error(detail);
    } else {
      toast.error(ts('toast_error', language));
    }
  };

  const replaceRow = (staff) => setTeam((rows) => rows.map((r) => (r.id === staff.id ? staff : r)));

  // ---- create ----
  const submitCreate = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    setCreateErrors({});
    try {
      await api.post('/sales/team', {
        name: createForm.name,
        email: createForm.email,
        phone: createForm.phone,
        password: createForm.password,
        role_keys: can(P.assign) ? createForm.role_keys : [],
      });
      toast.success(ts('toast_created', language));
      setCreateOpen(false);
      setCreateForm(EMPTY_CREATE);
      fetchAll();
    } catch (err) { showServerError(err, setCreateErrors); }
  };

  const toggleCreateRole = (key, checked) =>
    setCreateForm((f) => ({ ...f, role_keys: checked ? [...f.role_keys, key] : f.role_keys.filter((k) => k !== key) }));

  // ---- edit ----
  const openEdit = (row) => {
    setEditing(row);
    setEditForm({ name: row.name, email: row.email, phone: row.phone });
    setEditErrors({});
  };

  const submitEdit = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    setEditErrors({});
    const changes = {};
    ['name', 'email', 'phone'].forEach((k) => { if (editForm[k] !== editing[k]) changes[k] = editForm[k]; });
    if (Object.keys(changes).length === 0) { setEditing(null); return; }
    try {
      const res = await api.patch(`/sales/team/${editing.id}`, changes);
      replaceRow(res.data);
      toast.success(ts('toast_updated', language));
      setEditing(null);
    } catch (err) { showServerError(err, setEditErrors); }
  };

  // ---- activate / deactivate ----
  const toggleStatus = async (row) => {
    const deactivating = row.status === 'active';
    if (!window.confirm(ts(deactivating ? 'confirm_deactivate' : 'confirm_activate', language))) return;
    try {
      const res = await api.patch(`/sales/team/${row.id}`, { status: deactivating ? 'inactive' : 'active' });
      replaceRow(res.data);
      toast.success(ts('toast_updated', language));
    } catch (err) { showServerError(err); }
  };

  // ---- roles (applied immediately, one call per change) ----
  const rolesTarget = team.find((r) => r.id === rolesTargetId) || null;

  const toggleRole = async (row, role, checked) => {
    setBusyRole(role.key);
    try {
      const res = checked
        ? await api.post(`/sales/team/${row.id}/roles`, { role_key: role.key })
        : await api.delete(`/sales/team/${row.id}/roles/${role.key}`);
      replaceRow(res.data.staff);
      toast.success(ts(res.data.changed ? (checked ? 'toast_role_granted' : 'toast_role_revoked') : 'toast_role_unchanged', language));
    } catch (err) { showServerError(err); }
    setBusyRole(null);
  };

  // ---- reset password ----
  const submitReset = async (e) => {
    e.preventDefault();
    if (!validateAndFocus(e.currentTarget)) return;
    setResetErrors({});
    try {
      await api.post(`/sales/team/${resetting.id}/reset-password`, { new_password: newPassword });
      toast.success(ts('toast_password_reset', language));
      setResetting(null);
      setNewPassword('');
    } catch (err) { showServerError(err, setResetErrors); }
  };

  const fieldError = (errors, name, testid) =>
    errors[name] ? <p className="text-xs text-red-600 mt-1" data-testid={testid}>{errors[name]}</p> : null;

  // ---- render ----
  if (accessLoading) {
    return (
      <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
        <div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>
      </Layout>
    );
  }

  if (!hasModule('team')) {
    return (
      <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
        <SalesNoAccess language={language} />
      </Layout>
    );
  }

  return (
    <Layout userRole={userRole} onLogout={onLogout} language={language} setLanguage={setLanguage}>
      <div className="space-y-6">
        <div className="flex justify-between items-center gap-4">
          <div>
            <h1 className="text-4xl font-bold text-[#0A0A0A]" data-testid="sales-team-title">{ts('team_title', language)}</h1>
            {!canManage && <p className="text-sm text-gray-500 mt-1" data-testid="sales-team-view-only">{ts('team_view_only', language)}</p>}
          </div>
          {can(P.create) && (
            <Button data-testid="add-staff-btn" onClick={() => { setCreateForm(EMPTY_CREATE); setCreateErrors({}); setCreateOpen(true); }} className="bg-[#0033A0] hover:bg-[#002277] rounded-sm">
              <Plus className="w-4 h-4 me-2" />{ts('team_add', language)}
            </Button>
          )}
        </div>

        {loading ? (
          <div className="text-center py-12 text-gray-500">{ts('loading', language)}</div>
        ) : team.length === 0 ? (
          <Card className="p-12 text-center bg-white border border-gray-200" data-testid="sales-team-empty">
            <Users className="w-12 h-12 mx-auto text-gray-300 mb-4" />
            <p className="text-gray-500">{ts('team_empty', language)}</p>
          </Card>
        ) : (
          <Card className="bg-white border border-gray-200 rounded-md overflow-x-auto">
            <Table data-testid="sales-team-table">
              <TableHeader>
                <TableRow>
                  <TableHead className="text-start">{ts('team_col_name', language)}</TableHead>
                  <TableHead className="text-start">{ts('team_col_email', language)}</TableHead>
                  <TableHead className="text-start">{ts('team_col_phone', language)}</TableHead>
                  <TableHead className="text-start">{ts('team_col_roles', language)}</TableHead>
                  <TableHead className="text-start">{ts('team_col_status', language)}</TableHead>
                  {canManage && <TableHead className="text-start">{ts('team_col_actions', language)}</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {team.map((row) => (
                  <TableRow key={row.id} data-testid={`staff-row-${row.id}`}>
                    <TableCell className="font-medium">{row.name}</TableCell>
                    <TableCell dir="ltr" className="text-start">{row.email}</TableCell>
                    <TableCell dir="ltr" className="text-start">{row.phone}</TableCell>
                    <TableCell>
                      {row.roles.length === 0 ? (
                        <span className="text-xs text-gray-400">{ts('team_no_roles', language)}</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {row.roles.map((r) => (
                            <span key={r.key} className="px-2 py-0.5 rounded-full text-xs font-medium border bg-blue-50 text-[#0033A0] border-blue-200">{roleName(r, language)}</span>
                          ))}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${row.status === 'active' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-50 text-gray-700 border-gray-200'}`}>
                        {ts(row.status === 'active' ? 'status_active' : 'status_inactive', language)}
                      </span>
                    </TableCell>
                    {canManage && (
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {can(P.update) && (
                            <Button size="sm" variant="outline" className="rounded-sm" data-testid={`edit-staff-${row.id}`} onClick={() => openEdit(row)}>
                              <Pencil className="w-3.5 h-3.5 me-1" />{ts('action_edit', language)}
                            </Button>
                          )}
                          {can(P.assign) && (
                            <Button size="sm" variant="outline" className="rounded-sm" data-testid={`roles-staff-${row.id}`} onClick={() => setRolesTargetId(row.id)}>
                              <ShieldCheck className="w-3.5 h-3.5 me-1" />{ts('action_roles', language)}
                            </Button>
                          )}
                          {can(P.update) && (
                            <>
                              <Button size="sm" variant="outline" className="rounded-sm" data-testid={`reset-staff-${row.id}`} onClick={() => { setResetting(row); setNewPassword(''); setResetErrors({}); }}>
                                <KeyRound className="w-3.5 h-3.5 me-1" />{ts('action_reset_password', language)}
                              </Button>
                              <Button size="sm" variant="outline" className="rounded-sm" data-testid={`toggle-staff-${row.id}`} onClick={() => toggleStatus(row)}>
                                <Power className="w-3.5 h-3.5 me-1" />{ts(row.status === 'active' ? 'action_deactivate' : 'action_activate', language)}
                              </Button>
                            </>
                          )}
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>

      {/* Create */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent data-testid="create-staff-dialog">
          <DialogHeader><DialogTitle>{ts('dlg_create_title', language)}</DialogTitle></DialogHeader>
          <form onSubmit={submitCreate} noValidate className="space-y-4">
            <div>
              <Label htmlFor="cs-name">{ts('field_name', language)}</Label>
              <Input id="cs-name" required value={createForm.name} onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })} data-testid="cs-name" />
              {fieldError(createErrors, 'name', 'cs-name-error')}
            </div>
            <div>
              <Label htmlFor="cs-email">{ts('field_email', language)}</Label>
              <Input id="cs-email" type="email" required dir="ltr" value={createForm.email} onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })} data-testid="cs-email" />
              {fieldError(createErrors, 'email', 'cs-email-error')}
            </div>
            <div>
              <Label htmlFor="cs-phone">{ts('field_phone', language)}</Label>
              <Input id="cs-phone" type="tel" required dir="ltr" value={createForm.phone} onChange={(e) => setCreateForm({ ...createForm, phone: e.target.value })} data-testid="cs-phone" />
              {fieldError(createErrors, 'phone', 'cs-phone-error')}
            </div>
            <div>
              <Label htmlFor="cs-password">{ts('field_password', language)}</Label>
              <Input id="cs-password" type="password" required minLength={6} autoComplete="new-password" dir="ltr" value={createForm.password} onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })} data-testid="cs-password" />
              {fieldError(createErrors, 'password', 'cs-password-error')}
            </div>
            {can(P.assign) && (
              <div>
                <Label>{ts('field_initial_roles', language)}</Label>
                <div className="space-y-2 mt-2">
                  {roles.map((r) => (
                    <label key={r.key} className="flex items-center gap-2 text-sm cursor-pointer">
                      <Checkbox checked={createForm.role_keys.includes(r.key)} onCheckedChange={(c) => toggleCreateRole(r.key, c === true)} data-testid={`cs-role-${r.key}`} />
                      {roleName(r, language)}
                    </label>
                  ))}
                </div>
                {fieldError(createErrors, 'role_keys', 'cs-roles-error')}
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" className="rounded-sm" onClick={() => setCreateOpen(false)}>{ts('action_cancel', language)}</Button>
              <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="cs-submit">{ts('action_create', language)}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent data-testid="edit-staff-dialog">
          <DialogHeader><DialogTitle>{ts('dlg_edit_title', language)}</DialogTitle></DialogHeader>
          <form onSubmit={submitEdit} noValidate className="space-y-4">
            <div>
              <Label htmlFor="es-name">{ts('field_name', language)}</Label>
              <Input id="es-name" required value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} data-testid="es-name" />
              {fieldError(editErrors, 'name', 'es-name-error')}
            </div>
            <div>
              <Label htmlFor="es-email">{ts('field_email', language)}</Label>
              <Input id="es-email" type="email" required dir="ltr" value={editForm.email} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} data-testid="es-email" />
              {fieldError(editErrors, 'email', 'es-email-error')}
            </div>
            <div>
              <Label htmlFor="es-phone">{ts('field_phone', language)}</Label>
              <Input id="es-phone" type="tel" required dir="ltr" value={editForm.phone} onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })} data-testid="es-phone" />
              {fieldError(editErrors, 'phone', 'es-phone-error')}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" className="rounded-sm" onClick={() => setEditing(null)}>{ts('action_cancel', language)}</Button>
              <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="es-submit">{ts('action_save', language)}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Roles */}
      <Dialog open={!!rolesTarget} onOpenChange={(o) => !o && setRolesTargetId(null)}>
        <DialogContent data-testid="staff-roles-dialog">
          <DialogHeader>
            <DialogTitle>{ts('dlg_roles_title', language)}{rolesTarget ? ` - ${rolesTarget.name}` : ''}</DialogTitle>
            <DialogDescription>{ts('dlg_roles_hint', language)}</DialogDescription>
          </DialogHeader>
          {rolesTarget && (
            <div className="space-y-3">
              {roles.map((r) => {
                const has = rolesTarget.roles.some((x) => x.key === r.key);
                return (
                  <label key={r.key} className="flex items-center gap-3 text-sm cursor-pointer" data-testid={`role-row-${r.key}`}>
                    <Checkbox checked={has} disabled={busyRole === r.key} onCheckedChange={(c) => toggleRole(rolesTarget, r, c === true)} data-testid={`role-toggle-${r.key}`} />
                    <span>{roleName(r, language)}</span>
                  </label>
                );
              })}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" className="rounded-sm" onClick={() => setRolesTargetId(null)}>{ts('action_close', language)}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset password */}
      <Dialog open={!!resetting} onOpenChange={(o) => !o && setResetting(null)}>
        <DialogContent data-testid="reset-staff-dialog">
          <DialogHeader>
            <DialogTitle>{ts('dlg_reset_title', language)}{resetting ? ` - ${resetting.name}` : ''}</DialogTitle>
            <DialogDescription>{ts('dlg_reset_hint', language)}</DialogDescription>
          </DialogHeader>
          <form onSubmit={submitReset} noValidate className="space-y-4">
            <div>
              <Label htmlFor="rp-password">{ts('field_new_password', language)}</Label>
              <Input id="rp-password" type="password" required minLength={6} autoComplete="new-password" dir="ltr" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} data-testid="rp-password" />
              {fieldError(resetErrors, 'new_password', 'rp-password-error')}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" className="rounded-sm" onClick={() => setResetting(null)}>{ts('action_cancel', language)}</Button>
              <Button type="submit" className="bg-[#0033A0] hover:bg-[#002277] rounded-sm" data-testid="rp-submit">{ts('action_reset_password', language)}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Layout>
  );
};

export default SalesTeam;
