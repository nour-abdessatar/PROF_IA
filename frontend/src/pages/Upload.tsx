import { useState } from "react";
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { FileUp, Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { uploadCourse, uploadTest } from "@/lib/api";
import { t, useLang } from "@/lib/i18n";

type Status = "idle" | "uploading" | "ok" | "error";

function UploadCard({
  title,
  onSubmit,
}: {
  title: string;
  onSubmit: (file: File) => Promise<void>;
}) {
  const lang = useLang();
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string>("");

  const handle = async () => {
    if (!file) return;
    setStatus("uploading");
    setMessage("");
    try {
      await onSubmit(file);
      setStatus("ok");
      setMessage(file.name);
    } catch (e: any) {
      setStatus("error");
      setMessage(e?.message ?? String(e));
    }
  };

  return (
    <div className="glass-card p-6 space-y-4">
      <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
        <FileUp className="w-5 h-5 text-primary" /> {title}
      </h2>
      <label className="block">
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-sm text-muted-foreground file:mr-3 file:py-2 file:px-3 file:rounded-lg file:border-0 file:bg-primary file:text-primary-foreground file:font-medium hover:file:opacity-90"
        />
      </label>
      <motion.button
        whileTap={{ scale: 0.97 }}
        disabled={!file || status === "uploading"}
        onClick={handle}
        className="w-full py-3 rounded-xl gradient-primary text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 disabled:opacity-50"
      >
        {status === "uploading" ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        {t("upload_btn", lang)}
      </motion.button>
      {status === "ok" && (
        <p className="text-xs text-emerald-600 flex items-center gap-1.5">
          <CheckCircle2 className="w-4 h-4" /> {message}
        </p>
      )}
      {status === "error" && (
        <p className="text-xs text-destructive flex items-center gap-1.5">
          <AlertCircle className="w-4 h-4" /> {message}
        </p>
      )}
    </div>
  );
}

export default function Upload() {
  const navigate = useNavigate();
  const lang = useLang();
  return (
    <div className="min-h-screen bg-background px-6 py-12" dir={lang === "ar" ? "rtl" : "ltr"}>
      <div className="max-w-3xl mx-auto space-y-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gradient">Upload</h1>
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-muted-foreground hover:text-primary"
          >
            ← {t("back", lang)}
          </button>
        </div>
        <div className="grid sm:grid-cols-2 gap-5">
          <UploadCard title={t("upload_course_title", lang)} onSubmit={(f) => uploadCourse(f)} />
          <UploadCard title={t("upload_test_title", lang)} onSubmit={(f) => uploadTest(f)} />
        </div>
      </div>
    </div>
  );
}