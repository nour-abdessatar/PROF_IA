import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import AdminLayout from "@/components/AdminLayout";
import { apiGet } from "@/lib/api";

interface UserRow {
  id: string;
  name: string;
  email: string;
  joined_at: string;
  last_active: string | null;
  total_sessions: number;
}

export default function AdminUsers() {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<{ users: UserRow[] }>("/admin/users")
      .then((r) => setRows(r.users ?? []))
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, []);

  const fmt = (d: string | null) => (d ? new Date(d).toLocaleString() : "—");

  return (
    <AdminLayout title="Students">
      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Email</th>
                <th className="text-left font-medium px-5 py-3">Joined</th>
                <th className="text-left font-medium px-5 py-3">Last active</th>
                <th className="text-left font-medium px-5 py-3">Sessions</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={5} className="px-5 py-10 text-center text-muted-foreground">No students yet.</td></tr>
              ) : rows.map((u) => (
                <tr key={u.id} className="border-t border-border hover:bg-muted/30">
                  <td className="px-5 py-3 font-medium">
                    <Link to={`/admin/users/${u.id}`} className="text-primary hover:underline">
                      {u.name || "—"}
                    </Link>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">{u.email}</td>
                  <td className="px-5 py-3 text-muted-foreground">{fmt(u.joined_at)}</td>
                  <td className="px-5 py-3 text-muted-foreground">{fmt(u.last_active)}</td>
                  <td className="px-5 py-3 text-muted-foreground">{u.total_sessions ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AdminLayout>
  );
}