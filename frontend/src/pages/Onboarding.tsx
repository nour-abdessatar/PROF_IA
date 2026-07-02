import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { getLang, setLang, type Lang } from "@/lib/i18n";
import { useAuth } from "@/contexts/AuthContext";
import { setUserPref } from "@/lib/userPrefs";
import { Globe, User, Sparkles, ArrowRight, Bot } from "lucide-react";

const steps = ["lang", "name", "welcome"] as const;

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [selectedLang, setSelectedLang] = useState<Lang>(() => getLang());
  const [username, setUsername] = useState("");
  const [aiName, setAiName] = useState("Prof IA");
  const [typedText, setTypedText] = useState("");
  const navigate = useNavigate();
  const { user } = useAuth();

  const isAr = selectedLang === "ar";

  useEffect(() => {
    if (step === 2) {
      const msg = isAr
        ? `مرحبا ${username}! أنا ${aiName}، مساعدك الذكي. اطرح علي سؤالاً 😊`
        : `Salut ${username} ! Je suis ${aiName}, ton assistant intelligent. Pose-moi une question 😊`;
      let i = 0;
      setTypedText("");
      const interval = setInterval(() => {
        i++;
        setTypedText(msg.slice(0, i));
        if (i >= msg.length) clearInterval(interval);
      }, 28);
      return () => clearInterval(interval);
    }
  }, [step, username, aiName, isAr]);

  const next = () => {
    if (step === 0) setLang(selectedLang);
    if (step < 2) setStep(step + 1);
    else {
      const displayName = username.trim() || (selectedLang === "ar" ? "طالب" : "Élève");
      const assistantName = aiName.trim() || "Prof IA";

      setLang(selectedLang);
      setUserPref(user?.id, "onboarded", "true");
      setUserPref(user?.id, "lang", selectedLang);
      setUserPref(user?.id, "username", displayName);
      setUserPref(user?.id, "ai-name", assistantName);
      navigate("/dashboard");
    }
  };

  const labels = {
    titleLang: isAr ? "اختر لغتك" : "Choisissez votre langue",
    titleName: isAr ? "كيف تريد أن أناديك؟" : "Comment veux-tu que je t'appelle ?",
    titleAi: isAr ? "كيف تريد تسمية مساعدك؟" : "Comment veux-tu nommer ton avatar ?",
    welcome: isAr ? `مرحبا، ${username}!` : `Bienvenue, ${username} !`,
    placeholderUser: isAr ? "اسمك" : "Ton prénom",
    placeholderAi: isAr ? "اسم المساعد" : "Nom de l'assistant",
    next: isAr ? "التالي" : "Suivant",
    finish: isAr ? "ابدأ المغامرة" : "Commencer l'aventure",
  };

  const slideVariants = {
    enter: { opacity: 0, x: 60, scale: 0.95 },
    center: { opacity: 1, x: 0, scale: 1 },
    exit: { opacity: 0, x: -60, scale: 0.95 },
  };

  return (
    <div dir={isAr ? "rtl" : "ltr"} className="relative min-h-screen flex items-center justify-center px-6 overflow-hidden bg-gradient-to-br from-primary-light via-background to-accent/40">
      {/* Animated background blobs */}
      <motion.div
        animate={{ x: [0, 40, 0], y: [0, -30, 0], scale: [1, 1.1, 1] }}
        transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
        className="absolute top-10 -left-20 w-80 h-80 rounded-full bg-primary/30 blur-3xl"
      />
      <motion.div
        animate={{ x: [0, -50, 0], y: [0, 40, 0], scale: [1, 1.2, 1] }}
        transition={{ duration: 14, repeat: Infinity, ease: "easeInOut" }}
        className="absolute bottom-0 -right-20 w-96 h-96 rounded-full bg-accent/40 blur-3xl"
      />
      <motion.div
        animate={{ rotate: [0, 360] }}
        transition={{ duration: 40, repeat: Infinity, ease: "linear" }}
        className="absolute top-1/3 right-1/4 w-64 h-64 rounded-full bg-secondary/30 blur-3xl"
      />

      <div className="relative w-full max-w-md z-10">
        {/* Progress */}
        <div className="flex gap-2 mb-8 justify-center">
          {steps.map((_, i) => (
            <motion.div
              key={i}
              initial={false}
              animate={{ width: i <= step ? 44 : 24 }}
              className={`h-1.5 rounded-full transition-colors duration-500 ${
                i <= step ? "bg-gradient-to-r from-primary to-accent shadow-[0_0_12px_hsl(var(--primary)/0.5)]" : "bg-muted"
              }`}
            />
          ))}
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            variants={slideVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            className="relative bg-card/80 backdrop-blur-xl border border-border/50 rounded-3xl p-8 shadow-2xl shadow-primary/10"
          >
            {/* Glow ring */}
            <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-primary/10 via-transparent to-accent/10 pointer-events-none" />

            {step === 0 && (
              <div className="relative text-center space-y-6">
                <motion.div
                  initial={{ scale: 0, rotate: -180 }}
                  animate={{ scale: 1, rotate: 0 }}
                  transition={{ type: "spring", stiffness: 200, delay: 0.1 }}
                  className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/30"
                >
                  <Globe className="w-8 h-8 text-primary-foreground" />
                </motion.div>
                <h2 className="text-2xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                  {labels.titleLang}
                </h2>
                <div className="grid grid-cols-2 gap-4">
                  {([["fr", "Français", "🇫🇷"], ["ar", "العربية", "🇹🇳"]] as const).map(([code, label, flag], idx) => (
                    <motion.button
                      key={code}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.2 + idx * 0.1 }}
                      whileHover={{ scale: 1.05, y: -2 }}
                      whileTap={{ scale: 0.97 }}
                      onClick={() => setSelectedLang(code)}
                      className={`relative py-5 rounded-2xl font-semibold text-sm transition-all border-2 overflow-hidden ${
                        selectedLang === code
                          ? "border-primary bg-gradient-to-br from-primary/20 to-accent/20 text-primary shadow-lg shadow-primary/20"
                          : "border-border bg-card/50 text-foreground hover:border-primary/40"
                      }`}
                    >
                      <div className="text-3xl mb-1">{flag}</div>
                      <div>{label}</div>
                      {selectedLang === code && (
                        <motion.div
                          layoutId="lang-check"
                          className="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary flex items-center justify-center"
                        >
                          <Sparkles className="w-3 h-3 text-primary-foreground" />
                        </motion.div>
                      )}
                    </motion.button>
                  ))}
                </div>
              </div>
            )}

            {step === 1 && (
              <div className="relative space-y-5">
                <motion.div
                  initial={{ scale: 0, rotate: -180 }}
                  animate={{ scale: 1, rotate: 0 }}
                  transition={{ type: "spring", stiffness: 200 }}
                  className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/30"
                >
                  <User className="w-8 h-8 text-primary-foreground" />
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="space-y-2">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-1">
                    {labels.titleName}
                  </label>
                  <input
                    autoFocus
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder={labels.placeholderUser}
                    className="w-full px-4 py-3.5 rounded-xl bg-muted/40 border-2 border-border text-sm focus:outline-none focus:ring-4 focus:ring-primary/20 focus:border-primary transition-all"
                  />
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="space-y-2">
                  <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider px-1 flex items-center gap-1.5">
                    <Bot className="w-3.5 h-3.5" />
                    {labels.titleAi}
                  </label>
                  <input
                    value={aiName}
                    onChange={(e) => setAiName(e.target.value)}
                    placeholder={labels.placeholderAi}
                    className="w-full px-4 py-3.5 rounded-xl bg-muted/40 border-2 border-border text-sm focus:outline-none focus:ring-4 focus:ring-accent/20 focus:border-accent transition-all"
                  />
                </motion.div>
              </div>
            )}

            {step === 2 && (
              <div className="relative space-y-6">
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 180 }}
                  className="relative w-20 h-20 mx-auto"
                >
                  <motion.div
                    animate={{ scale: [1, 1.15, 1], opacity: [0.5, 0.2, 0.5] }}
                    transition={{ duration: 2, repeat: Infinity }}
                    className="absolute inset-0 rounded-2xl bg-gradient-to-br from-primary to-accent blur-xl"
                  />
                  <div className="relative w-full h-full rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/40">
                    <Sparkles className="w-10 h-10 text-primary-foreground" />
                  </div>
                </motion.div>
                <h2 className="text-2xl font-bold text-center bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                  {labels.welcome}
                </h2>
                <div className="bg-gradient-to-br from-muted/60 to-primary-light/40 rounded-2xl p-4 min-h-[90px] border border-border/50">
                  <div className={`flex items-start gap-3 ${isAr ? "flex-row-reverse" : ""}`}>
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center shrink-0 shadow-md">
                      <Bot className="w-5 h-5 text-primary-foreground" />
                    </div>
                    <p className="text-sm text-foreground leading-relaxed pt-1.5">
                      {typedText}
                      <span className="inline-block w-0.5 h-4 bg-primary ml-0.5 animate-pulse align-middle" />
                    </p>
                  </div>
                </div>
              </div>
            )}

            <motion.button
              whileHover={{ scale: 1.02, y: -1 }}
              whileTap={{ scale: 0.97 }}
              onClick={next}
              disabled={step === 1 && !username}
              className={`relative w-full mt-6 py-3.5 rounded-xl bg-gradient-to-r from-primary to-accent text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 shadow-lg shadow-primary/30 hover:shadow-xl hover:shadow-primary/40 transition-shadow disabled:opacity-50 disabled:cursor-not-allowed overflow-hidden group ${isAr ? "flex-row-reverse" : ""}`}
            >
              <span className="absolute inset-0 bg-gradient-to-r from-accent to-primary opacity-0 group-hover:opacity-100 transition-opacity" />
              <span className="relative">{step < 2 ? labels.next : labels.finish}</span>
              <ArrowRight className={`relative w-4 h-4 transition-transform group-hover:translate-x-1 ${isAr ? "rotate-180 group-hover:-translate-x-1" : ""}`} />
            </motion.button>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
