import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import AdminLayout from "@/components/AdminLayout";
import { apiGet } from "@/lib/api";

type Status = "pending_review" | "indexing" | "ready";
interface CourseRow {
  id: string;
  name: string;
  status: Status;
  uploaded_at: string;
}

const statusStyle: Record<Status, string> = {
  pending_review: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  indexing: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
};

export default function AdminCourses() {
  const [rows, setRows] = useState<CourseRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<{ courses: CourseRow[] }>("/admin/courses")
      .then((r) => setRows(r.courses ?? []))
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AdminLayout title="Courses">
      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="text-left font-medium px-5 py-3">Name</th>
                <th className="text-left font-medium px-5 py-3">Status</th>
                <th className="text-left font-medium px-5 py-3">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={3} className="px-5 py-10 text-center text-muted-foreground">No courses yet.</td></tr>
              ) : rows.map((c) => (
                <tr key={c.id} className="border-t border-border">
                  <td className="px-5 py-3 font-medium text-foreground">{c.name}</td>
                  <td className="px-5 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusStyle[c.status] ?? ""}`}>
                      {c.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">
                    {c.uploaded_at ? new Date(c.uploaded_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AdminLayout>
  );
}