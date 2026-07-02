import { useState } from "react";
import { Upload as UploadIcon, Loader2, Check, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import AdminLayout from "@/components/AdminLayout";
import { apiPost } from "@/lib/api";

type StepStatus = "pending" | "running" | "done" | "error";
interface Step { key: string; label: string; status: StepStatus; detail?: string }

const INITIAL_STEPS: Step[] = [
  { key: "upload", label: "Uploading PDF", status: "pending" },
  { key: "extract", label: "Extracting pages & text", status: "pending" },
  { key: "render", label: "Rendering images", status: "pending" },
  { key: "ocr", label: "OCR & text analysis", status: "pending" },
  { key: "ready", label: "Ready for review", status: "pending" },
];

export default function AdminUpload() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [steps, setSteps] = useState<Step[]>(INITIAL_STEPS);
  const [busy, setBusy] = useState(false);

  const setStep = (key: string, patch: Partial<Step>) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const start = async () => {
    if (!file) return toast.error("Choose a PDF first");
    setBusy(true);
    setSteps(INITIAL_STEPS.map((s) => ({ ...s, status: "pending" })));
    try {
      setStep("upload", { status: "running" });
      const form = new FormData();
      form.append("file", file);
      if (title) form.append("title", title);
      const res = await apiPost<{ steps?: { key: string; status: StepStatus; detail?: string }[]; course_id?: string }>(
        "/admin/upload",
        form,
      );
      // Mark all reported steps; otherwise mark all done
      if (res.steps && res.steps.length) {
        for (const s of res.steps) setStep(s.key, { status: s.status, detail: s.detail });
      } else {
        for (const s of INITIAL_STEPS) setStep(s.key, { status: "done" });
      }
      toast.success("Pipeline completed. Continue to review images.");
      setTimeout(() => navigate("/admin/review-images"), 600);
    } catch (e: any) {
      const failing = steps.find((s) => s.status === "running")?.key ?? "upload";
      setStep(failing, { status: "error", detail: e.message });
      toast.error(`Upload failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AdminLayout title="Upload course PDF">
      <div className="max-w-2xl space-y-6">
        <div className="space-y-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Course title (optional)"
            className="w-full px-3 py-2.5 rounded-lg border border-border bg-card text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          <label className="flex items-center gap-3 p-4 rounded-lg border-2 border-dashed border-border hover:border-primary/40 cursor-pointer transition-colors">
            <UploadIcon className="w-5 h-5 text-primary" />
            <span className="text-sm text-muted-foreground flex-1 truncate">
              {file ? file.name : "Choose a PDF file"}
            </span>
            <input
              type="file"
              accept="application/pdf,.pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
          </label>
          <button
            onClick={start}
            disabled={busy || !file}
            className="px-5 py-2.5 rounded-lg gradient-primary text-primary-foreground text-sm font-medium flex items-center gap-2 disabled:opacity-60"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            Start processing
          </button>
        </div>

        <div className="rounded-xl border border-border bg-card p-5 space-y-3">
          <p className="text-sm font-semibold text-foreground">Pipeline progress</p>
          <ol className="space-y-2">
            {steps.map((s) => (
              <li key={s.key} className="flex items-center gap-3 text-sm">
                <span className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 border border-border bg-background">
                  {s.status === "done" && <Check className="w-3.5 h-3.5 text-primary" />}
                  {s.status === "running" && <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />}
                  {s.status === "error" && <AlertCircle className="w-3.5 h-3.5 text-destructive" />}
                </span>
                <div className="min-w-0">
                  <p className={s.status === "error" ? "text-destructive" : "text-foreground"}>{s.label}</p>
                  {s.detail && <p className="text-xs text-muted-foreground truncate">{s.detail}</p>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </AdminLayout>
  );
}