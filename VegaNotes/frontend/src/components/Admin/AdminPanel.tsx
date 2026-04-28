import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

type AdminUser = { name: string; is_admin: boolean; has_password: boolean };

/**
 * Admin-only user-management panel.
 *
 * Lists every user in the DB, lets the admin:
 *   - create a new login (name + password + optional admin flag)
 *   - reset a user's password
 *   - toggle admin
 *   - delete a user
 *
 * Per-project membership (manager/member) is managed elsewhere from the
 * Sidebar's project context menu.
 */
export function AdminPanel() {
  const qc = useQueryClient();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const { data: users = [], error, isLoading } = useQuery<AdminUser[]>({
    queryKey: ["admin", "users"],
    queryFn: () => api.adminListUsers(),
    enabled: !!me?.is_admin,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "users"] });
  const reportError = (e: any) => alert(`${e?.message ?? e}`);

  const [reindexResult, setReindexResult] = useState<string | null>(null);
  const reindex = useMutation({
    mutationFn: () => api.adminReindex(),
    onSuccess: (r) => {
      setReindexResult(`✓ Reindexed ${r.files_indexed} files`);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      qc.invalidateQueries({ queryKey: ["tree"] });
      // The reindex itself is read-only on disk, but if the user just
      // edited an .md file outside the app the editor's cached body /
      // etag are now stale. Invalidating these makes the editor's
      // useQuery refetch and live-sync the new disk content into the
      // draft (when there are no unsaved local edits) — fixes #145.
      qc.invalidateQueries({ queryKey: ["notes"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
    onError: reportError,
  });

  const createUser = useMutation({
    mutationFn: (v: { name: string; password: string; is_admin: boolean }) =>
      api.adminCreateUser(v.name, v.password, v.is_admin),
    onSuccess: refresh,
    onError: reportError,
  });
  const patchUser = useMutation({
    mutationFn: (v: { name: string; patch: { password?: string; is_admin?: boolean } }) =>
      api.adminPatchUser(v.name, v.patch),
    onSuccess: refresh,
    onError: reportError,
  });
  const deleteUser = useMutation({
    mutationFn: (name: string) => api.adminDeleteUser(name),
    onSuccess: refresh,
    onError: reportError,
  });

  if (me && !me.is_admin) {
    return <div className="p-6 text-rose-700">Admin role required.</div>;
  }

  return (
    <div className="p-6 max-w-4xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">Users</h2>
          <p className="text-sm text-slate-500">
            Logged in as <code>{me?.name ?? "…"}</code>{me?.is_admin ? " (admin)" : ""}.
            New users get a password here, then can be added to projects by
            right-clicking a project in the Sidebar and choosing
            <em> Manage members…</em> (managers/admin only).
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <button
            onClick={() => { setReindexResult(null); reindex.mutate(); }}
            disabled={reindex.isPending}
            className="rounded bg-slate-700 text-white px-3 py-1.5 text-sm disabled:opacity-50 whitespace-nowrap"
          >
            {reindex.isPending ? "Refreshing…" : "⟳ Refresh DB"}
          </button>
          {reindexResult && (
            <span className="text-xs text-emerald-700">{reindexResult}</span>
          )}
        </div>
      </div>

      <CreateUserForm
        busy={createUser.isPending}
        onSubmit={(v) => createUser.mutate(v)}
      />

      {isLoading && <div className="text-sm text-slate-500">Loading…</div>}
      {error && <div className="text-rose-700 text-sm">{(error as Error).message}</div>}

      <table className="w-full text-sm border">
        <thead className="bg-slate-100 text-left">
          <tr>
            <th className="p-2">Name</th>
            <th className="p-2">Role</th>
            <th className="p-2">Password</th>
            <th className="p-2 w-1/2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <UserRow
              key={u.name}
              user={u}
              isSelf={u.name === me?.name}
              onResetPassword={(pw) => patchUser.mutate({ name: u.name, patch: { password: pw } })}
              onToggleAdmin={() => patchUser.mutate({ name: u.name, patch: { is_admin: !u.is_admin } })}
              onDelete={() => {
                if (window.confirm(`Delete user "${u.name}"? Their project memberships are removed too.`)) {
                  deleteUser.mutate(u.name);
                }
              }}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreateUserForm({
  busy, onSubmit,
}: {
  busy: boolean;
  onSubmit: (v: { name: string; password: string; is_admin: boolean }) => void;
}) {
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [admin, setAdmin] = useState(false);
  return (
    <form
      className="flex flex-wrap gap-2 items-end border rounded p-3 bg-slate-50"
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim() || !pw) return;
        onSubmit({ name: name.trim(), password: pw, is_admin: admin });
        setName(""); setPw(""); setAdmin(false);
      }}
    >
      <label className="flex flex-col text-xs">
        <span className="text-slate-600">Username</span>
        <input className="border rounded px-2 py-1 text-sm" value={name}
          onChange={(e) => setName(e.target.value)} placeholder="e.g. alice" />
      </label>
      <label className="flex flex-col text-xs">
        <span className="text-slate-600">Password</span>
        <input className="border rounded px-2 py-1 text-sm" type="password" value={pw}
          onChange={(e) => setPw(e.target.value)} />
      </label>
      <label className="flex items-center gap-1 text-xs">
        <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} />
        admin
      </label>
      <button type="submit" disabled={busy || !name.trim() || !pw}
        className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50">
        {busy ? "creating…" : "+ create user"}
      </button>
    </form>
  );
}

function UserRow({
  user, isSelf, onResetPassword, onToggleAdmin, onDelete,
}: {
  user: AdminUser;
  isSelf: boolean;
  onResetPassword: (pw: string) => void;
  onToggleAdmin: () => void;
  onDelete: () => void;
}) {
  const [editingPw, setEditingPw] = useState(false);
  const [pw, setPw] = useState("");
  return (
    <tr className="border-t">
      <td className="p-2 font-mono">{user.name}{isSelf && " (you)"}</td>
      <td className="p-2">{user.is_admin ? "admin" : "user"}</td>
      <td className="p-2">{user.has_password
        ? <span className="text-emerald-700">set</span>
        : <span className="text-amber-700">not set — cannot log in</span>}</td>
      <td className="p-2 flex flex-wrap gap-2">
        {editingPw ? (
          <form
            className="flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (!pw) return;
              onResetPassword(pw);
              setPw(""); setEditingPw(false);
            }}
          >
            <input className="border rounded px-2 py-0.5 text-xs" type="password"
              autoFocus value={pw} onChange={(e) => setPw(e.target.value)} placeholder="new password" />
            <button className="rounded border px-2 py-0.5 text-xs" type="submit">save</button>
            <button className="rounded border px-2 py-0.5 text-xs" type="button"
              onClick={() => { setEditingPw(false); setPw(""); }}>cancel</button>
          </form>
        ) : (
          <button className="rounded border px-2 py-0.5 text-xs" onClick={() => setEditingPw(true)}>
            {user.has_password ? "reset password" : "set password"}
          </button>
        )}
        <button className="rounded border px-2 py-0.5 text-xs" onClick={onToggleAdmin} disabled={isSelf}>
          {user.is_admin ? "demote" : "promote to admin"}
        </button>
        <button className="rounded border border-rose-300 text-rose-700 px-2 py-0.5 text-xs"
          onClick={onDelete} disabled={isSelf}>
          delete
        </button>
      </td>
    </tr>
  );
}
