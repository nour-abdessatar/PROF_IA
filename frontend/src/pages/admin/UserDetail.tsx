import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import AdminLayout from "@/components/AdminLayout";
import { apiGet } from "@/lib/api";

interface UserDetail {
  id: string;
  name: string;
  email: string;
  joined_at: string;
  last_active: string | null;
  level?: string;
  total_sessions?: number;
  tests: { id: string; title: string; score: number; total: number; taken_at: string }[];
  chats: { id: string; title: string; last_message: string; updated_at: string }[];
}

export default function AdminUserDetail() {
  const { user_id } = useParams();
  const [data, setData] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user_id) return;
    apiGet<UserDetail>(`/admin/users/${user_id}`)
      .then(setData)
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [user_id]);

  return (
    <AdminLayout title="Student profile">
      <Link to="/admin/users" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-6">
        <ArrowLeft className="w-4 h-4" /> Back to students
      </Link>

      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : !data ? (
        <div className="text-center py-16 text-muted-foreground rounded-xl border border-border">Student not found.</div>
      ) : (
        <div className="space-y-8">
          <section className="rounded-xl border border-border bg-card p-6">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-full gradient-primary flex items-center justify-center text-primary-foreground font-bold text-lg">
                {(data.name || data.email || "?")[0]?.toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="text-lg font-semibold text-foreground">{data.name || "—"}</p>
                <p className="text-sm text-muted-foreground">{data.email}</p>
              </div>
            </div>
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-6 text-sm">
              <Stat label="Joined" value={data.joined_at ? new Date(data.joined_at).toLocaleDateString() : "—"} />
              <Stat label="Last active" value={data.last_active ? new Date(data.last_active).toLocaleDateString() : "—"} />
              <Stat label="Level" value={data.level ?? "—"} />
              <Stat label="Sessions" value={String(data.total_sessions ?? 0)} />
            </dl>
          </section>

          <section>
            <h2 className="text-sm font-semibold text-foreground mb-3">Test history</h2>
            <div className="rounded-xl border border-border bg-card overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-muted-foreground">
                  <tr>
                    <th className="text-left font-medium px-5 py-3">Title</th>
                    <th className="text-left font-medium px-5 py-3">Score</th>
                    <th className="text-left font-medium px-5 py-3">Taken</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.tests ?? []).length === 0 ? (
                    <tr><td colSpan={3} className="px-5 py-8 text-center text-muted-foreground">No tests yet.</td></tr>
                  ) : data.tests.map((t) => (
                    <tr key={t.id} className="border-t border-border">
                      <td className="px-5 py-3 font-medium">{t.title}</td>
                      <td className="px-5 py-3 text-muted-foreground">{t.score}/{t.total}</td>
                      <td className="px-5 py-3 text-muted-foreground">{new Date(t.taken_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2 className="text-sm font-semibold text-foreground mb-3">Chat history</h2>
            <div className="space-y-2">
              {(data.chats ?? []).length === 0 ? (
                <div className="text-center py-8 text-muted-foreground rounded-xl border border-border bg-card">
                  No chat sessions yet.
                </div>
              ) : data.chats.map((c) => (
                <div key={c.id} className="rounded-xl border border-border bg-card p-4 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <p className="font-medium text-foreground truncate">{c.title || "Untitled chat"}</p>
                    <p className="text-xs text-muted-foreground truncate mt-0.5">{c.last_message}</p>
                  </div>
                  <span className="text-xs text-muted-foreground shrink-0">
                    {new Date(c.updated_at).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </AdminLayout>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium text-foreground mt-0.5">{value}</dd>
    </div>
  );
}