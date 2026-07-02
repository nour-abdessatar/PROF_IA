import { useEffect, useState } from "react";

export type Lang = "fr" | "ar";

export const translations = {
  fr: {
    hero_title: "Ton assistant éducatif intelligent",
    hero_badge: "🎓 Plateforme éducative IA",
    hero_line1: "Ton assistant",
    hero_line2: "éducatif intelligent",
    hero_subtitle: "Pose une question et reçois une explication claire basée sur tes cours",
    start: "Commencer",
    explore: "Explorer",
    video1_title: "Notre environnement scolaire",
    video2_title: "Ton assistant intelligent",
    features: ["Mode examen", "Multilingue", "Programme tunisien", "Avatar interactif", "Personnalisation", "Sources fiables", "Explications simples", "Mode vocal"],
    how_to_use: "Comment utiliser",
    how_to_use_desc: "Pose une question sur ton cours et Prof IA t'explique simplement avec des exemples.",
    about: "À propos",
    about_desc: "Prof IA est un assistant éducatif basé sur l'IA, conçu pour les élèves tunisiens du collège.",
    reviews: "Avis utilisateurs",
    reviews_desc: "\"Prof IA m'a aidé à comprendre les maths en 5 minutes !\" — Élève, 8ème année",
    login: "Se connecter",
    signup: "Créer un compte",
    email: "Email",
    password: "Mot de passe",
    forgot_password: "Mot de passe oublié ?",
    show_password: "Afficher le mot de passe",
    hide_password: "Masquer le mot de passe",
    onboarding_lang: "Choisissez votre langue",
    onboarding_name: "Comment tu t'appelles ?",
    onboarding_ai_name: "Nom de ton assistant",
    onboarding_welcome: "Bienvenue",
    username: "Nom d'utilisateur",
    ai_name: "Nom de l'assistant",
    next: "Suivant",
    finish: "Terminer",
    chat: "Chat",
    test: "Test",
    courses: "Cours",
    resume: "Reprendre dernière activité",
    ask_question: "Pose ta question…",
    references: "Voir les références",
    history: "Historique",
    chat_mode: "Chat",
    exercise_mode: "Exercice",
    ref_title: "Références du cours",
    ref_source: "Source",
    ref_excerpt: "Extrait",
    ref_concepts: "Concepts clés",
    not_found_title: "Page introuvable",
    not_found_desc: "Cette page n'existe pas ou a été déplacée.",
    back_home: "Retour à l'accueil",
    show_avatar: "Voir le prof",
    rights: "Tous droits réservés.",
    choose_subject: "Choisis ta matière",
    subject_locked: "Verrouillé — abonne-toi pour y accéder",
    upload_course_title: "Téléverser un cours (PDF)",
    upload_test_title: "Téléverser un test (PDF)",
    upload_btn: "Téléverser",
    results_title: "Résultats du test",
    score: "Score",
    feedback: "Retour",
    back: "Retour",
    subj_math: "Mathématiques",
    subj_science: "Sciences",
    subj_physique: "Physique",
    subj_arabe: "Arabe",
    subj_francais: "Français",
    subj_histoire: "Histoire",
    subj_geographie: "Géographie",
  },
  ar: {
    hero_title: "مساعدك التعليمي الذكي",
    hero_badge: "🎓 منصة تعليمية بالذكاء الاصطناعي",
    hero_line1: "مساعدك",
    hero_line2: "التعليمي الذكي",
    hero_subtitle: "اطرح سؤالاً واحصل على شرح واضح من دروسك",
    start: "ابدأ",
    explore: "استكشف",
    video1_title: "بيئتنا المدرسية",
    video2_title: "مساعدك الذكي",
    features: ["وضع الامتحان", "متعدد اللغات", "المنهج التونسي", "أفاتار تفاعلي", "تخصيص", "مصادر موثوقة", "شروحات بسيطة", "الوضع الصوتي"],
    how_to_use: "كيفية الاستخدام",
    how_to_use_desc: "اطرح سؤالاً حول درسك وسيشرح لك Prof IA ببساطة مع أمثلة.",
    about: "حول",
    about_desc: "Prof IA هو مساعد تعليمي قائم على الذكاء الاصطناعي مصمم لتلاميذ الإعدادي في تونس.",
    reviews: "آراء المستخدمين",
    reviews_desc: "\"ساعدني Prof IA على فهم الرياضيات في 5 دقائق!\" — تلميذ، السنة 8",
    login: "تسجيل الدخول",
    signup: "إنشاء حساب",
    email: "البريد الإلكتروني",
    password: "كلمة المرور",
    forgot_password: "هل نسيت كلمة المرور؟",
    show_password: "إظهار كلمة المرور",
    hide_password: "إخفاء كلمة المرور",
    onboarding_lang: "اختر لغتك",
    onboarding_name: "ما اسمك؟",
    onboarding_ai_name: "اسم مساعدك",
    onboarding_welcome: "مرحبًا",
    username: "اسم المستخدم",
    ai_name: "اسم المساعد",
    next: "التالي",
    finish: "إنهاء",
    chat: "محادثة",
    test: "اختبار",
    courses: "دروس",
    resume: "استئناف آخر نشاط",
    ask_question: "اطرح سؤالك…",
    references: "عرض المراجع",
    history: "السجل",
    chat_mode: "محادثة",
    exercise_mode: "تمرين",
    ref_title: "مراجع الدرس",
    ref_source: "المصدر",
    ref_excerpt: "مقتطف",
    ref_concepts: "المفاهيم الأساسية",
    not_found_title: "الصفحة غير موجودة",
    not_found_desc: "هذه الصفحة غير موجودة أو تم نقلها.",
    back_home: "العودة إلى الرئيسية",
    show_avatar: "عرض الأستاذ",
    rights: "جميع الحقوق محفوظة.",
    choose_subject: "اختر مادتك",
    subject_locked: "مقفلة — اشترك للوصول",
    upload_course_title: "رفع درس (PDF)",
    upload_test_title: "رفع اختبار (PDF)",
    upload_btn: "رفع",
    results_title: "نتائج الاختبار",
    score: "النتيجة",
    feedback: "ملاحظات",
    back: "رجوع",
    subj_math: "الرياضيات",
    subj_science: "العلوم",
    subj_physique: "الفيزياء",
    subj_arabe: "العربية",
    subj_francais: "الفرنسية",
    subj_histoire: "التاريخ",
    subj_geographie: "الجغرافيا",
  },
} as const;

const LANG_KEY = "profia-lang";
const LANG_EVENT = "profia-lang-change";

export function t(key: keyof typeof translations.fr, lang: Lang = "fr"): string {
  const val = translations[lang][key];
  if (Array.isArray(val)) return val.join(", ");
  return val as string;
}

export function getFeatures(lang: Lang): string[] {
  return translations[lang].features as unknown as string[];
}

export function getLang(): Lang {
  if (typeof window === "undefined") return "fr";
  return (localStorage.getItem(LANG_KEY) as Lang) || "fr";
}

export function setLang(lang: Lang) {
  localStorage.setItem(LANG_KEY, lang);
  window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: lang }));
  // Update <html dir> globally for native form controls / scrollbars
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  document.documentElement.lang = lang;
}

/** Reactive hook — re-renders when language changes anywhere in the app */
export function useLang(): Lang {
  const [lang, setLangState] = useState<Lang>(() => getLang());

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as Lang | undefined;
      setLangState(detail ?? getLang());
    };
    window.addEventListener(LANG_EVENT, handler);
    // Also react to storage changes from other tabs
    const storageHandler = (e: StorageEvent) => {
      if (e.key === LANG_KEY && e.newValue) setLangState(e.newValue as Lang);
    };
    window.addEventListener("storage", storageHandler);
    // Initial sync of <html dir>
    document.documentElement.dir = getLang() === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = getLang();
    return () => {
      window.removeEventListener(LANG_EVENT, handler);
      window.removeEventListener("storage", storageHandler);
    };
  }, []);

  return lang;
}
