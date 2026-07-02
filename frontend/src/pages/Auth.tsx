import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { t, useLang } from "@/lib/i18n";
import { Mail, Lock, ArrowRight, Eye, EyeOff, Sparkles } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

export default function Auth() {
  const [tab, setTab] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");
  const navigate = useNavigate();
  const lang = useLang();
  const isAr = lang === "ar";
  const { signIn, signUp, user, role, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    if (user && role) {
      navigate(role === "admin" ? "/admin" : "/dashboard", { replace: true });
    }
  }, [user, role, loading, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");
    if (!email.includes("@")) {
      setError(isAr ? "البريد الإلكتروني غير صالح" : "Email invalide");
      return;
    }
    if (password.length < 6) {
      setError(isAr ? "كلمة المرور قصيرة جداً (6 أحرف على الأقل)" : "Mot de passe trop court (6 caractères min.)");
      return;
    }
    setBusy(true);
    const { error: err } = tab === "login"
      ? await signIn(email.trim(), password)
      : await signUp(email.trim(), password);
    setBusy(false);
    if (err) {
      setError(err);
      return;
    }
    if (tab === "signup") {
      setInfo(isAr
        ? "تم إنشاء الحساب. تحقق من بريدك الإلكتروني إذا كان التأكيد مفعّلاً."
        : "Compte créé. Vérifie ton email si la confirmation est activée.");
      return; // useEffect above will redirect once session arrives
    }
    // login: useEffect will redirect once role loads
  };

  return (
    <div
      dir={isAr ? "rtl" : "ltr"}
      className="relative min-h-screen flex items-center justify-center bg-gradient-to-br from-primary-light via-background to-accent/30 px-6 overflow-hidden"
    >
      <div className="absolute -top-20 -left-20 w-80 h-80 rounded-full bg-primary/20 blur-3xl pointer-events-none" />
      <div className="absolute -bottom-20 -right-20 w-96 h-96 rounded-full bg-accent/20 blur-3xl pointer-events-none" />

      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="relative w-full max-w-md z-10"
      >
        <div className="text-center mb-8">
          <motion.div
            initial={{ scale: 0, rotate: -180 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ type: "spring", stiffness: 200, delay: 0.1 }}
            className="w-14 h-14 mx-auto mb-4 rounded-2xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/30"
          >
            <Sparkles className="w-7 h-7 text-primary-foreground" />
          </motion.div>
          <h1 className="text-3xl font-bold text-gradient">Prof IA</h1>
          <p className="text-muted-foreground text-sm mt-2">
            {tab === "login" ? t("login", lang) : t("signup", lang)}
          </p>
        </div>

        <div className="glass-card p-8">
          <div className="flex bg-muted rounded-xl p-1 mb-8">
            {(["login", "signup"] as const).map((tb) => (
              <button
                key={tb}
                onClick={() => { setTab(tb); setError(""); }}
                className={`flex-1 py-2.5 text-sm font-medium rounded-lg transition-all duration-300 ${
                  tab === tb ? "bg-card shadow-sm text-foreground" : "text-muted-foreground"
                }`}
              >
                {tb === "login" ? t("login", lang) : t("signup", lang)}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            <motion.form
              key={tab}
              initial={{ opacity: 0, x: tab === "login" ? -10 : 10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: tab === "login" ? 10 : -10 }}
              transition={{ duration: 0.25 }}
              onSubmit={handleSubmit}
              className="space-y-5"
            >
              <div className="relative group">
                <Mail className={`absolute ${isAr ? "right-4" : "left-4"} top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground group-focus-within:text-primary transition-colors`} />
                <input
                  type="email"
                  placeholder={t("email", lang)}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                  className={`w-full ${isAr ? "pr-11 pl-4" : "pl-11 pr-4"} py-3.5 rounded-xl bg-muted/50 border border-border text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all`}
                />
              </div>

              <div className="relative group">
                <Lock className={`absolute ${isAr ? "right-4" : "left-4"} top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground group-focus-within:text-primary transition-colors`} />
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder={t("password", lang)}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete={tab === "login" ? "current-password" : "new-password"}
                  className={`w-full ${isAr ? "pr-11 pl-11" : "pl-11 pr-11"} py-3.5 rounded-xl bg-muted/50 border border-border text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all`}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? t("hide_password", lang) : t("show_password", lang)}
                  className={`absolute ${isAr ? "left-3" : "right-3"} top-1/2 -translate-y-1/2 p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-muted transition-colors`}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>

              {error && (
                <motion.p
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="text-xs text-destructive font-medium px-1"
                >
                  {error}
                </motion.p>
              )}

              {tab === "login" && (
                <div className={`flex ${isAr ? "justify-start" : "justify-end"}`}>
                  <button
                    type="button"
                    className="text-xs text-primary font-medium hover:underline"
                  >
                    {t("forgot_password", lang)}
                  </button>
                </div>
              )}

              <motion.button
                type="submit"
                disabled={busy}
                whileHover={{ scale: 1.01 }}
                whileTap={{ scale: 0.98 }}
                className={`w-full py-3.5 rounded-xl gradient-primary text-primary-foreground font-semibold text-sm flex items-center justify-center gap-2 shadow-soft hover:shadow-hover transition-shadow disabled:opacity-60 disabled:cursor-not-allowed ${isAr ? "flex-row-reverse" : ""}`}
              >
                {busy ? "…" : (tab === "login" ? t("login", lang) : t("signup", lang))}
                <ArrowRight className={`w-4 h-4 ${isAr ? "rotate-180" : ""}`} />
              </motion.button>
              {info && (
                <p className="text-xs text-primary font-medium px-1">{info}</p>
              )}
            </motion.form>
          </AnimatePresence>
        </div>
      </motion.div>
    </div>
  );
}
