import { motion } from "framer-motion";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { BookOpen, Info, Star, Sparkles, Zap, GraduationCap } from "lucide-react";
import FeatureCarousel from "@/components/FeatureCarousel";
import LandingVideo from "@/components/LandingVideo";
import ScrollAnimation from "@/components/ScrollAnimation";
import { t, setLang, useLang, type Lang } from "@/lib/i18n";
import { LANDING_BG_FRAME } from "@/lib/scrollFrames";
import { useAuth } from "@/contexts/AuthContext";
import { getUserLang } from "@/lib/userPrefs";

// Landing page is always displayed in French regardless of app language
const LANDING_LANG: Lang = "fr";

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.15, duration: 0.6 } }),
};

export default function Landing() {
  const navigate = useNavigate();
  const { user } = useAuth();

  useEffect(() => {
    // Force French while on landing page
    setLang("fr");
    document.documentElement.dir = "ltr";
    document.documentElement.lang = "fr";

    return () => {
      // Restore user's real language preference when leaving landing
      const savedLang = getUserLang(user?.id);
      if (savedLang) {
        setLang(savedLang);
        document.documentElement.dir = savedLang === "ar" ? "rtl" : "ltr";
        document.documentElement.lang = savedLang;
      }
    };
  }, []);

  const infoCards = [
    { icon: BookOpen, title: t("how_to_use", LANDING_LANG), desc: t("how_to_use_desc", LANDING_LANG), color: "from-primary to-accent" },
    { icon: Info,     title: t("about", LANDING_LANG),      desc: t("about_desc", LANDING_LANG),      color: "from-emerald-500 to-teal-500" },
    { icon: Star,     title: t("reviews", LANDING_LANG),    desc: t("reviews_desc", LANDING_LANG),    color: "from-amber-500 to-orange-500" },
  ];

  return (
    <div className="min-h-screen bg-background" dir="ltr" lang={LANDING_LANG}>
      {/* Nav */}
      <nav className="fixed top-0 w-full z-50 glass border-b border-border/50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <span className="text-xl font-bold text-gradient">Prof IA</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate("/auth")}
              className="px-5 py-2 text-sm font-medium rounded-xl gradient-primary text-primary-foreground hover:opacity-90 transition-opacity"
            >
              {t("login", LANDING_LANG)}
            </button>
          </div>
        </div>
      </nav>

      {/* Full-screen scroll intro (plays first) */}
      <ScrollAnimation />

      {/* Rest of landing — frame 087 background */}
      <div
        className="relative -mt-px bg-cover bg-center bg-fixed bg-no-repeat"
        style={{ backgroundImage: `url(${LANDING_BG_FRAME})` }}
      >
        <div className="absolute inset-0 z-0 bg-white/78 backdrop-blur-[1px] pointer-events-none" aria-hidden />
        <div className="relative z-20">

          {/* Stats bar */}
          <motion.section
            className="relative pt-16 pb-12 px-6"
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true, amount: 0.2 }}
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="max-w-3xl mx-auto rounded-2xl bg-black/20 backdrop-blur-sm py-6 px-4">
              <motion.div
                className="flex flex-wrap items-center justify-center gap-8 sm:gap-12"
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.6, delay: 0.1 }}
              >
                {[
                  { icon: GraduationCap, label: "500+ élèves" },
                  { icon: Zap,           label: "Réponse < 2s" },
                  { icon: Sparkles,      label: "Programme officiel" },
                ].map((stat, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-sm text-white drop-shadow-[0_1px_4px_rgba(0,0,0,0.45)]"
                  >
                    <stat.icon className="w-4 h-4 text-white" />
                    <span className="font-medium">{stat.label}</span>
                  </div>
                ))}
              </motion.div>
            </div>
          </motion.section>

          {/* Videos — always LTR layout on landing */}
          <section className="pb-28 px-6 relative z-20">
            <div className="max-w-5xl mx-auto relative" style={{ minHeight: 420 }}>
              <LandingVideo
                src="/videos/school-environment.mp4"
                fromStart={true}
                className="relative z-10 md:w-[58%] w-full"
              />
              <LandingVideo
                src="/videos/ai-assistant.mp4"
                fromStart={false}
                className="relative z-20 md:w-[50%] w-[90%] md:ml-auto md:-mt-24 mt-6 ml-auto"
              />
            </div>
          </section>

          {/* Carousel — always fr */}
          <FeatureCarousel lang={LANDING_LANG} />

          {/* Info Cards */}
          <section className="py-20 px-6">
            <div className="max-w-5xl mx-auto grid md:grid-cols-3 gap-6">
              {infoCards.map((card, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.12, duration: 0.5 }}
                  whileHover={{ y: -4 }}
                  className="glass-card p-6 space-y-3 group"
                >
                  <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${card.color} flex items-center justify-center transition-transform group-hover:scale-110`}>
                    <card.icon className="w-5 h-5 text-primary-foreground" />
                  </div>
                  <h3 className="font-semibold text-foreground">{card.title}</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">{card.desc}</p>
                </motion.div>
              ))}
            </div>
          </section>

          {/* Footer */}
          <footer className="py-8 border-t border-black/10 bg-white/90 backdrop-blur-sm">
            <div className="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-2 text-sm text-black">
              <span className="font-semibold text-black">Prof IA</span>
              <span className="text-black">
                © {new Date().getFullYear()} Prof IA. {t("rights", LANDING_LANG)}
              </span>
            </div>
          </footer>

        </div>
      </div>
    </div>
  );
}