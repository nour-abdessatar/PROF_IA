// Re-export the auto-managed Lovable Cloud client so the rest of the app
// keeps importing from "@/lib/supabase".
export { supabase } from "@/integrations/supabase/client";

// Hardcoded admin email — must match the value in the handle_new_user() DB trigger.
export const ADMIN_EMAIL = "admin@college.tn";

export type AppRole = "admin" | "student";
export type Subject =
  | "math"
  | "physique"
  | "francais"
  | "arabe"
  | "histoire"
  | "geographie"
  | "science"
  | "anglais"
  | "islamique"
  | "civique";
export type Level = "7" | "8" | "9";

export const SUBJECTS: { value: Subject; fr: string; ar: string; icon: string }[] = [
  { value: "math", fr: "Mathématiques", ar: "الرياضيات", icon: "🧮" },
  { value: "physique", fr: "Physique", ar: "الفيزياء", icon: "⚛️" },
  { value: "francais", fr: "Français", ar: "الفرنسية", icon: "🇫🇷" },
  { value: "arabe", fr: "Arabe", ar: "العربية", icon: "🇹🇳" },
  { value: "histoire", fr: "Histoire", ar: "التاريخ", icon: "📜" },
  { value: "geographie", fr: "Géographie", ar: "الجغرافيا", icon: "🌍" },
  { value: "science", fr: "Sciences", ar: "العلوم", icon: "🔬" },
  { value: "anglais", fr: "Anglais", ar: "الإنجليزية", icon: "🇬🇧" },
  { value: "islamique", fr: "Éducation islamique", ar: "تربية إسلامية", icon: "🕌" },
  { value: "civique", fr: "Éducation civique", ar: "تربية مدنية", icon: "⚖️" },
];

export const LEVELS: { value: Level; fr: string; ar: string }[] = [
  { value: "7", fr: "7ème année", ar: "السابعة أساسي" },
  { value: "8", fr: "8ème année", ar: "الثامنة أساسي" },
  { value: "9", fr: "9ème année", ar: "التاسعة أساسي" },
];