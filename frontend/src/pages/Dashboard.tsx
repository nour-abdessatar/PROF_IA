import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { MessageCircle, FlaskConical, BookOpen, RotateCcw, Globe, GraduationCap, Sparkles, ArrowRight, CheckCircle2 } from "lucide-react";
import { t, useLang, setLang, type Lang } from "@/lib/i18n";
import { useAuth } from "@/contexts/AuthContext";
import { getUserPref, removeUserPref, setUserPref } from "@/lib/userPrefs";

const fadeUp = (i: number) => ({
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0, transition: { delay: i * 0.1, duration: 0.5 } },
});

type AssessStep = "level" | "test-invite" | "done";

export default function Dashboard() {
  const navigate = useNavigate();
  const lang = useLang();
  const { user } = useAuth();
  const userId = user?.id;
  const username = getUserPref(userId, "username", "Élève");
  const aiName = getUserPref(userId, "ai-name", "Prof IA");
  const isAr = lang === "ar";

  const [assessDone, setAssessDone] = useState<boolean>(
    () => getUserPref(userId, "assessment-done") === "true"
  );
  const [step, setStep] = useState<AssessStep>("level");
  const [level, setLevel] = useState<string>(
    () => getUserPref(userId, "level")
  );

  useEffect(() => {
    if (assessDone) setStep("done");
  }, [assessDone]);

  useEffect(() => {
    setAssessDone(getUserPref(userId, "assessment-done") === "true");
    setLevel(getUserPref(userId, "level"));
  }, [userId]);

  const cards = [
    { icon: MessageCircle, label: t("chat", lang), color: "from-[hsl(248_65%_59%)] to-[hsl(250_85%_72%)]", path: "/chat?mode=chat" },
    { icon: FlaskConical, label: t("test", lang), color: "from-amber-400 to-orange-400", path: "/chat?mode=exercise" },
    { icon: BookOpen, label: t("courses", lang), color: "from-emerald-400 to-teal-500", path: "/chat?mode=courses" },
    { icon: RotateCcw, label: t("resume", lang), color: "from-rose-400 to-pink-500", path: "/chat?mode=resume" },
  ];

  const toggleLang = () => {
    const newLang: Lang = lang === "fr" ? "ar" : "fr";
    setLang(newLang);
    setUserPref(userId, "lang", newLang);
  };

  const resetAssessment = () => {
    removeUserPref(userId, "assessment-done");
    removeUserPref(userId, "test-skipped");
    removeUserPref(userId, "level");
    setLevel("");
    setAssessDone(false);
    setStep("level");
  };

  const levels = [
    { value: "7", label: isAr ? "السابعة أساسي" : "7ème année" },
    { value: "8", label: isAr ? "الثامنة أساسي" : "8ème année" },
    { value: "9", label: isAr ? "التاسعة أساسي" : "9ème année" },
  ];

  const labels = {
    levelTitle: isAr ? "ما هو مستواك؟" : "Quel est ton niveau ?",
    levelSub: isAr
      ? "لكي نفهمك بشكل أفضل، يرجى تحديد مستواك."
      : "Pour mieux te comprendre, peux-tu indiquer ton niveau ?",
    next: isAr ? "التالي" : "Suivant",
    testTitle: isAr ? "اختبار سريع للتعرف عليك" : "Un petit test pour mieux te connaître",
    testSub: isAr
      ? "هل يمكنك إجراء هذا الاختبار السريع حتى نتعرف عليك بشكل أفضل؟"
      : "Peux-tu passer ce test rapide afin que nous apprenions à mieux te connaître ?",
    later: isAr ? "لاحقاً" : "Plus tard",
    continue: isAr ? "متابعة" : "Continuer",
    skipped: isAr ? "تم التخطي" : "Test ignoré",
  };

  const saveLevel = () => {
    if (!level) return;
    setUserPref(userId, "level", level);
    setStep("test-invite");
  };

  const finishAssessment = (skipped = false) => {
    setUserPref(userId, "assessment-done", "true");
    if (skipped) setUserPref(userId, "test-skipped", "true");
    setAssessDone(true);
    setStep("done");
    navigate("/subjects?next=dashboard");
  };

  return (
    <div className="min-h-screen bg-background" dir={isAr ? "rtl" : "ltr"}>
      {/* Top bar */}
      <nav className="sticky top-0 z-50 glass border-b border-border/50">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <span className="text-xl font-bold text-gradient">Prof IA</span>
          <div className="flex items-center gap-4">
            <button onClick={toggleLang} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-muted transition-colors text-sm text-muted-foreground">
              <Globe className="w-4 h-4" />
              {lang === "fr" ? "العربية" : "Français"}
            </button>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full gradient-primary flex items-center justify-center text-xs text-primary-foreground font-bold">
                {username[0]?.toUpperCase()}
              </div>
              <span className="text-sm font-medium text-foreground">{username}</span>
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-5xl mx-auto px-6 py-16">
        <motion.div initial="hidden" animate="visible" className="space-y-12">
          {/* Greeting */}
          <motion.div variants={fadeUp(0)} className="text-center space-y-3">
            <h1 className="text-3xl font-bold text-foreground">
              {isAr ? `مرحبا ${username} 👋` : `Salut ${username} 👋`}
            </h1>
            <p className="text-muted-foreground text-lg">{t("dashboard_greeting", lang)}</p>
          </motion.div>

          {/* Assessment flow (new user) */}
          <AnimatePresence mode="wait">
            {!assessDone && (
              <motion.div
                key={step}
                initial={{ opacity: 0, y: 20, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -20, scale: 0.98 }}
                transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                className="relative overflow-hidden rounded-3xl p-8 border border-border/50 bg-gradient-to-br from-primary/10 via-card to-accent/10 backdrop-blur-xl shadow-xl shadow-primary/10"
              >
                {/* Decorative blobs */}
                <div className="absolute -top-16 -right-16 w-56 h-56 rounded-full bg-primary/20 blur-3xl pointer-events-none" />
                <div className="absolute -bottom-16 -left-16 w-56 h-56 rounded-full bg-accent/20 blur-3xl pointer-events-none" />

                {step === "level" && (
                  <div className="relative space-y-6">
                    <div className={`flex items-center gap-3 ${isAr ? "flex-row-reverse text-right" : ""}`}>
                      <motion.div
                        initial={{ scale: 0, rotate: -180 }}
                        animate={{ scale: 1, rotate: 0 }}
                        transition={{ type: "spring", stiffness: 200 }}
                        className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/30 shrink-0"
                      >
                        <GraduationCap className="w-6 h-6 text-primary-foreground" />
                      </motion.div>
                      <div>
                        <h2 className="text-xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                          {labels.levelTitle}
                        </h2>
                        <p className="text-sm text-muted-foreground mt-0.5">{labels.levelSub}</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                      {levels.map((lv, idx) => (
                        <motion.button
                          key={lv.value}
                          initial={{ opacity: 0, y: 16 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: 0.1 + idx * 0.08 }}
                          whileHover={{ scale: 1.04, y: -2 }}
                          whileTap={{ scale: 0.97 }}
                          onClick={() => setLevel(lv.value)}
                          className={`relative py-5 px-3 rounded-2xl font-semibold text-sm border-2 transition-all overflow-hidden ${
                            level === lv.value
                              ? "border-primary bg-gradient-to-br from-primary/20 to-accent/20 text-primary shadow-lg shadow-primary/20"
                              : "border-border bg-card/50 text-foreground hover:border-primary/40"
                          }`}
                        >
                          <div className="text-2xl mb-1">📚</div>
                          <div>{lv.label}</div>
                          {level === lv.value && (
                            <motion.div
                              layoutId="level-check"
                              className="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary flex items-center justify-center"
                            >
                              <CheckCircle2 className="w-3.5 h-3.5 text-primary-foreground" />
                            </motion.div>
                          )}
                        </motion.button>
                      ))}
                    </div>

                    <motion.button
                      whileHover={{ scale: 1.02, y: -1 }}
                      whileTap={{ scale: 0.97 }}
                      disabled={!level}
                      onClick={saveLevel}
                      className={`w-full py-3.5 rounded-xl bg-gradient-to-r from-primary to-accent text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 shadow-lg shadow-primary/30 disabled:opacity-50 disabled:cursor-not-allowed ${isAr ? "flex-row-reverse" : ""}`}
                    >
                      <span>{labels.next}</span>
                      <ArrowRight className={`w-4 h-4 ${isAr ? "rotate-180" : ""}`} />
                    </motion.button>
                  </div>
                )}

                {step === "test-invite" && (
                  <div className="relative space-y-6">
                    <div className={`flex items-center gap-3 ${isAr ? "flex-row-reverse text-right" : ""}`}>
                      <motion.div
                        initial={{ scale: 0 }}
                        animate={{ scale: 1 }}
                        transition={{ type: "spring", stiffness: 200 }}
                        className="relative w-12 h-12 shrink-0"
                      >
                        <motion.div
                          animate={{ scale: [1, 1.15, 1], opacity: [0.5, 0.2, 0.5] }}
                          transition={{ duration: 2, repeat: Infinity }}
                          className="absolute inset-0 rounded-2xl bg-gradient-to-br from-primary to-accent blur-lg"
                        />
                        <div className="relative w-full h-full rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/40">
                          <Sparkles className="w-6 h-6 text-primary-foreground" />
                        </div>
                      </motion.div>
                      <div>
                        <h2 className="text-xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                          {labels.testTitle}
                        </h2>
                        <p className="text-sm text-muted-foreground mt-0.5">{labels.testSub}</p>
                      </div>
                    </div>

                    <div className={`flex flex-col sm:flex-row gap-3 ${isAr ? "sm:flex-row-reverse" : ""}`}>
                      <motion.button
                        whileHover={{ scale: 1.02, y: -1 }}
                        whileTap={{ scale: 0.97 }}
                        onClick={() => finishAssessment(false)}
                        className={`flex-1 py-3.5 rounded-xl bg-gradient-to-r from-primary to-accent text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 shadow-lg shadow-primary/30 ${isAr ? "flex-row-reverse" : ""}`}
                      >
                        <span>{labels.continue}</span>
                        <ArrowRight className={`w-4 h-4 ${isAr ? "rotate-180" : ""}`} />
                      </motion.button>
                      <motion.button
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.97 }}
                        onClick={() => finishAssessment(true)}
                        className="flex-1 py-3.5 rounded-xl border-2 border-border bg-card/50 text-foreground font-semibold text-sm hover:border-primary/40 transition-colors"
                      >
                        {labels.later}
                      </motion.button>
                    </div>
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* AI Greeting + activities (only after assessment) */}
          {assessDone && (
            <>
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                className="glass-card p-6 flex items-center gap-4"
              >
                <div className="w-12 h-12 rounded-2xl gradient-primary flex items-center justify-center shrink-0 animate-breathe">
                  <span className="text-lg">🤖</span>
                </div>
                <div>
                  <p className="font-semibold text-foreground">{aiName}</p>
                  <p className="text-sm text-muted-foreground">
                    {isAr ? "أنا جاهز لمساعدتك! اختر نشاطاً." : "Je suis prêt à t'aider ! Choisis une activité."}
                  </p>
                </div>
              </motion.div>

              <div className="grid sm:grid-cols-2 gap-4">
                {cards.map((card, i) => (
                  <motion.button
                    key={i}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.1 + i * 0.08, duration: 0.4 }}
                    whileHover={{ y: -4, scale: 1.01 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={() => navigate(card.path)}
                    className="glass-card p-6 flex items-center gap-4 text-left hover:shadow-hover transition-shadow w-full"
                  >
                    <div className={`w-12 h-12 rounded-2xl bg-gradient-to-br ${card.color} flex items-center justify-center shrink-0`}>
                      <card.icon className="w-6 h-6 text-primary-foreground" />
                    </div>
                    <span className="font-semibold text-foreground">{card.label}</span>
                  </motion.button>
                ))}
              </div>

              <div className="pt-4 flex justify-center">
                <button
                  onClick={resetAssessment}
                  className="text-xs text-muted-foreground hover:text-primary underline-offset-4 hover:underline transition-colors"
                >
                  {isAr ? "إعادة تقييم المستوى" : "Refaire l'évaluation de niveau"}
                </button>
              </div>
            </>
          )}
        </motion.div>
      </main>
    </div>
  );
}
