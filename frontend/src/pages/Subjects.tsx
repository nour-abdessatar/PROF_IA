import { motion } from "framer-motion";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Lock } from "lucide-react";
import { t, useLang } from "@/lib/i18n";
import { useAuth } from "@/contexts/AuthContext";
import {
  SUBJECT_KEYS,
  SUBJECT_ICONS,
  setSubject,
  getAllowedSubjects,
  type SubjectKey,
} from "@/lib/subjects";

export default function Subjects() {
  const navigate = useNavigate();
  const lang = useLang();
  const isAr = lang === "ar";
  const { user } = useAuth();
  const [params] = useSearchParams();
  const next = params.get("next") || "chat"; // dashboard | chat | exercise | courses
  const allowed = getAllowedSubjects();

  const label = (s: SubjectKey) => t(`subj_${s}` as any, lang);

  const pick = (s: SubjectKey) => {
    if (!allowed.includes(s)) return;
    setSubject(s, user?.id);
    if (next === "dashboard") navigate("/dashboard");
    else if (next === "exercise") navigate("/chat?mode=exercise");
    else if (next === "courses") navigate("/chat?mode=courses");
    else navigate("/chat?mode=chat");
  };

  return (
    <div className="min-h-screen bg-background px-6 py-12" dir={isAr ? "rtl" : "ltr"}>
      <div className="max-w-4xl mx-auto space-y-10">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold text-gradient">{t("choose_subject", lang)}</h1>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          {SUBJECT_KEYS.map((s, i) => {
            const locked = !allowed.includes(s);
            return (
              <motion.button
                key={s}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.04 }}
                whileHover={{ y: -3, scale: 1.02 }}
                whileTap={{ scale: 0.97 }}
                onClick={() => pick(s)}
                className={`relative aspect-square rounded-2xl border-2 p-4 flex flex-col items-center justify-center gap-2 text-center transition-all ${
                  locked
                    ? "border-border bg-muted/40 text-muted-foreground cursor-not-allowed"
                    : "border-border bg-card hover:border-primary/60 hover:shadow-lg text-foreground"
                }`}
              >
                <div className="text-4xl">{SUBJECT_ICONS[s]}</div>
                <div className="text-sm font-semibold">{label(s)}</div>
                {locked && (
                  <div className="absolute top-2 right-2 w-7 h-7 rounded-full bg-background/80 flex items-center justify-center">
                    <Lock className="w-3.5 h-3.5" />
                  </div>
                )}
              </motion.button>
            );
          })}
        </div>

        {next !== "dashboard" && (
          <div className="text-center">
            <button
              onClick={() => navigate("/dashboard")}
              className="text-sm text-muted-foreground hover:text-primary"
            >
              ← {t("back", lang)}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
