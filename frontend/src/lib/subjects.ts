export type SubjectKey =
  | "math"
  | "science"
  | "physique"
  | "arabe"
  | "francais"
  | "histoire"
  | "geographie";

export const SUBJECT_KEYS: SubjectKey[] = [
  "math",
  "science",
  "physique",
  "arabe",
  "francais",
  "histoire",
  "geographie",
];

export const SUBJECT_ICONS: Record<SubjectKey, string> = {
  math: "🧮",
  science: "🔬",
  physique: "⚛️",
  arabe: "🇹🇳",
  francais: "🇫🇷",
  histoire: "📜",
  geographie: "🌍",
};

const KEY = "subject";
const subjectKey = (userId: string) => `profia:${userId}:${KEY}`;

export const getSubject = (userId?: string): SubjectKey | null =>
  (typeof window !== "undefined" && userId ? (localStorage.getItem(subjectKey(userId)) as SubjectKey | null) : null);

export const setSubject = (s: SubjectKey, userId?: string) => {
  if (!userId) return;
  localStorage.setItem(subjectKey(userId), s);
};

// Subjects the student has access to (plan). For now everything is allowed;
// the backend will own the real entitlement check.
const PLAN_KEY = "profia-subjects-allowed";
export const getAllowedSubjects = (): SubjectKey[] => {
  if (typeof window === "undefined") return SUBJECT_KEYS;
  const raw = localStorage.getItem(PLAN_KEY);
  if (!raw) return SUBJECT_KEYS;
  try {
    return JSON.parse(raw) as SubjectKey[];
  } catch {
    return SUBJECT_KEYS;
  }
};
