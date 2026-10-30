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
  const [googleBusy, setGoogleBusy] = useState(false);
  const [info, setInfo] = useState("");
  const navigate = useNavigate();
  const lang = useLang();
  const isAr = lang === "ar";
  const { signIn, signUp, signInWithGoogle, user, role, loading } = useAuth();

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

  // NEW: Google OAuth. A successful call navigates the browser away to
  // Google's account picker immediately, so googleBusy only really shows
  // for the brief moment before that redirect kicks in. If the call itself
  // fails (e.g. provider not enabled in Supabase dashboard), we surface
  // that as a normal inline error same as email/password. If the user
  // cancels the Google picker and lands back on this page, there's no
  // error to show — it just looks like nothing happened, which is correct.
  const handleGoogleSignIn = async () => {
    setError("");
    setGoogleBusy(true);
    const { error: err } = await signInWithGoogle();
    setGoogleBusy(false);
    if (err) setError(err);
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

          <motion.button
            type="button"
            onClick={handleGoogleSignIn}
            disabled={googleBusy}
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.98 }}
            className={`w-full py-3.5 mb-5 rounded-xl bg-card border border-border text-foreground font-medium text-sm flex items-center justify-center gap-3 shadow-sm hover:shadow-hover transition-shadow disabled:opacity-60 disabled:cursor-not-allowed`}
          >
            {googleBusy ? (
              "…"
            ) : (
              <>
                <svg className="w-4 h-4 shrink-0" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
                  <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"/>
                  <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.9 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4c-7.5 0-13.9 4.2-17.2 10.4z"/>
                  <path fill="#4CAF50" d="M24 44c5.5 0 10.4-1.9 14.3-5.1l-6.6-5.6C29.6 35 26.9 36 24 36c-5.3 0-9.7-3.1-11.3-7.9l-6.6 5.1C9.9 39.8 16.4 44 24 44z"/>
                  <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.3-4.2 5.8l6.6 5.6C41.5 36.6 44 30.8 44 24c0-1.3-.1-2.7-.4-3.5z"/>
                </svg>
                {isAr ? "المتابعة باستخدام جوجل" : "Continuer avec Google"}
              </>
            )}
          </motion.button>

          <div className="flex items-center gap-3 mb-5">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">{isAr ? "أو" : "OU"}</span>
            <div className="h-px flex-1 bg-border" />
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