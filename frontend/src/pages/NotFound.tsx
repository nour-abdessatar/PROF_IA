import { useLocation, Link } from "react-router-dom";
import { useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Compass } from "lucide-react";
import { t, useLang } from "@/lib/i18n";

const NotFound = () => {
  const location = useLocation();
  const lang = useLang();
  const isAr = lang === "ar";

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  return (
    <div
      dir={isAr ? "rtl" : "ltr"}
      className="relative min-h-screen flex items-center justify-center px-6 overflow-hidden bg-gradient-to-br from-primary-light via-background to-accent/30"
    >
      <div className="absolute -top-20 -left-20 w-80 h-80 rounded-full bg-primary/20 blur-3xl" />
      <div className="absolute -bottom-20 -right-20 w-96 h-96 rounded-full bg-accent/20 blur-3xl" />

      <motion.div
        initial={{ opacity: 0, y: 20, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 text-center max-w-md"
      >
        <motion.div
          initial={{ scale: 0, rotate: -180 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: "spring", stiffness: 200, delay: 0.1 }}
          className="w-20 h-20 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/30"
        >
          <Compass className="w-10 h-10 text-primary-foreground" />
        </motion.div>

        <h1 className="text-7xl font-extrabold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent mb-3">
          404
        </h1>
        <h2 className="text-xl font-bold text-foreground mb-2">{t("not_found_title", lang)}</h2>
        <p className="text-muted-foreground mb-8">{t("not_found_desc", lang)}</p>

        <Link
          to="/"
          className={`inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-gradient-to-r from-primary to-accent text-primary-foreground font-semibold text-sm shadow-lg shadow-primary/30 hover:shadow-xl hover:shadow-primary/40 transition-shadow ${isAr ? "flex-row-reverse" : ""}`}
        >
          <ArrowLeft className={`w-4 h-4 ${isAr ? "rotate-180" : ""}`} />
          {t("back_home", lang)}
        </Link>
      </motion.div>
    </div>
  );
};

export default NotFound;
