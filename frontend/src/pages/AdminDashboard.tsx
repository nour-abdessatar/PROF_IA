import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  BookOpen, FlaskConical, LogOut, Globe, Plus, Trash2, Upload, FileText,
  ShieldCheck, Loader2, X, Check, ArrowLeft,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { supabase, SUBJECTS, LEVELS, type Subject, type Level } from "@/lib/supabase";
import { useLang, setLang, type Lang } from "@/lib/i18n";
import { toast } from "sonner";

type Tab = "courses" | "tests";

interface Course {
  id: string;
  title: string;
  description: string | null;
  subject: string;
  level: string;
  pdf_path: string | null;
}

interface TestRow {
  id: string;
  title: string;
  subject: string;
  level: string;
}

interface Question {
  id?: string;
  question: string;
  choices: string[];
  correct_index: number;
  position?: number;
}

export default function AdminDashboard() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const lang = useLang();
  const isAr = lang === "ar";
  const [tab, setTab] = useState<Tab>("courses");

  const tr = {
    title: isAr ? "لوحة الإدارة" : "Espace Admin",
    courses: isAr ? "الدروس" : "Cours",
    tests: isAr ? "اختبارات المستوى" : "Tests de niveau",
    logout: isAr ? "خروج" : "Déconnexion",
    admin: isAr ? "مدير" : "Administrateur",
  };

  return (
    <div className="min-h-screen bg-background" dir={isAr ? "rtl" : "ltr"}>
      <nav className="sticky top-0 z-50 glass border-b border-border/50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl gradient-primary flex items-center justify-center">
              <ShieldCheck className="w-4 h-4 text-primary-foreground" />
            </div>
            <div>
              <p className="text-sm font-bold text-foreground leading-tight">Prof IA</p>
              <p className="text-[10px] text-muted-foreground leading-tight">{tr.admin}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLang((lang === "fr" ? "ar" : "fr") as Lang)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-muted transition-colors text-sm text-muted-foreground"
            >
              <Globe className="w-4 h-4" />
              {lang === "fr" ? "العربية" : "Français"}
            </button>
            <span className="text-xs text-muted-foreground hidden sm:inline">{user?.email}</span>
            <button
              onClick={async () => { await signOut(); navigate("/auth"); }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-destructive/10 text-destructive transition-colors text-sm font-medium"
            >
              <LogOut className="w-4 h-4" />
              {tr.logout}
            </button>
          </div>
        </div>
      </nav>

      <main className="max-w-6xl mx-auto px-6 py-10">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-foreground">{tr.title}</h1>
          <p className="text-muted-foreground mt-1">
            {isAr ? "إدارة الدروس واختبارات المستوى لجميع المستويات والمواد." : "Gérer les cours et les tests pour toutes les matières et tous les niveaux."}
          </p>
        </div>

        <div className="flex gap-2 mb-8 p-1 bg-muted rounded-xl w-fit">
          {([
            { id: "courses" as Tab, icon: BookOpen, label: tr.courses },
            { id: "tests" as Tab, icon: FlaskConical, label: tr.tests },
          ]).map(({ id, icon: Icon, label }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                tab === id ? "bg-card text-foreground shadow-sm" : "text-muted-foreground"
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>

        <AnimatePresence mode="wait">
          {tab === "courses" && (
            <motion.div key="c" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <CoursesManager lang={lang} />
            </motion.div>
          )}
          {tab === "tests" && (
            <motion.div key="t" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <TestsManager lang={lang} />
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* COURSES                                                             */
/* ------------------------------------------------------------------ */
function CoursesManager({ lang }: { lang: Lang }) {
  const isAr = lang === "ar";
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [filterLevel, setFilterLevel] = useState<Level | "all">("all");
  const [filterSubject, setFilterSubject] = useState<Subject | "all">("all");

  const load = async () => {
    setLoading(true);
    const { data, error } = await supabase
      .from("courses")
      .select("*")
      .order("created_at", { ascending: false });
    if (error) toast.error(error.message);
    setCourses((data as Course[]) ?? []);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const remove = async (c: Course) => {
    if (!confirm(isAr ? "حذف هذا الدرس؟" : "Supprimer ce cours ?")) return;
    if (c.pdf_path) await supabase.storage.from("course-files").remove([c.pdf_path]);
    const { error } = await supabase.from("courses").delete().eq("id", c.id);
    if (error) return toast.error(error.message);
    toast.success(isAr ? "تم الحذف" : "Supprimé");
    load();
  };

  const filtered = courses.filter(c =>
    (filterLevel === "all" || c.level === filterLevel) &&
    (filterSubject === "all" || c.subject === filterSubject)
  );

  const subjectLabel = (v: string) => SUBJECTS.find(s => s.value === v) ?? { fr: v, ar: v, icon: "📘" };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <select
          value={filterLevel}
          onChange={e => setFilterLevel(e.target.value as Level | "all")}
          className="px-3 py-2 rounded-lg border border-border bg-card text-sm"
        >
          <option value="all">{isAr ? "كل المستويات" : "Tous les niveaux"}</option>
          {LEVELS.map(l => <option key={l.value} value={l.value}>{isAr ? l.ar : l.fr}</option>)}
        </select>
        <select
          value={filterSubject}
          onChange={e => setFilterSubject(e.target.value as Subject | "all")}
          className="px-3 py-2 rounded-lg border border-border bg-card text-sm"
        >
          <option value="all">{isAr ? "كل المواد" : "Toutes les matières"}</option>
          {SUBJECTS.map(s => <option key={s.value} value={s.value}>{s.icon} {isAr ? s.ar : s.fr}</option>)}
        </select>
        <button
          onClick={() => setOpen(true)}
          className="ml-auto flex items-center gap-2 px-4 py-2 rounded-lg gradient-primary text-primary-foreground font-medium text-sm shadow-soft hover:shadow-hover transition-shadow"
        >
          <Plus className="w-4 h-4" />
          {isAr ? "درس جديد" : "Nouveau cours"}
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground glass-card">
          {isAr ? "لا توجد دروس بعد." : "Aucun cours pour le moment."}
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map(c => {
            const sub = subjectLabel(c.subject);
            return (
              <motion.div
                key={c.id}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                className="glass-card p-5 flex flex-col gap-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-2xl">{sub.icon}</span>
                    <div className="min-w-0">
                      <p className="font-semibold text-foreground truncate">{c.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {isAr ? sub.ar : sub.fr} • {c.level}ème
                      </p>
                    </div>
                  </div>
                  <button onClick={() => remove(c)} className="p-1.5 rounded-md hover:bg-destructive/10 text-destructive">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                {c.description && <p className="text-sm text-muted-foreground line-clamp-2">{c.description}</p>}
                {c.pdf_path && (
                  <a
                    href={supabase.storage.from("course-files").getPublicUrl(c.pdf_path).data.publicUrl}
                    target="_blank" rel="noreferrer"
                    className="flex items-center gap-1.5 text-xs text-primary font-medium hover:underline"
                  >
                    <FileText className="w-3.5 h-3.5" />
                    {isAr ? "عرض PDF" : "Voir le PDF"}
                  </a>
                )}
              </motion.div>
            );
          })}
        </div>
      )}

      <CourseDialog open={open} onClose={() => setOpen(false)} onSaved={load} lang={lang} />
    </div>
  );
}

function CourseDialog({ open, onClose, onSaved, lang }: { open: boolean; onClose: () => void; onSaved: () => void; lang: Lang }) {
  const isAr = lang === "ar";
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [subject, setSubject] = useState<Subject>("math");
  const [level, setLevel] = useState<Level>("7");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => { setTitle(""); setDesc(""); setSubject("math"); setLevel("7"); setFile(null); };

  const save = async () => {
    if (!title.trim()) return toast.error(isAr ? "العنوان مطلوب" : "Titre requis");
    setBusy(true);
    let pdf_path: string | null = null;
    if (file) {
      const ext = file.name.split(".").pop() ?? "pdf";
      const path = `${subject}/${level}/${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`;
      const { error: upErr } = await supabase.storage.from("course-files").upload(path, file, { upsert: false });
      if (upErr) { setBusy(false); return toast.error(upErr.message); }
      pdf_path = path;
    }
    const { data: { user } } = await supabase.auth.getUser();
    const { error } = await supabase.from("courses").insert({
      title: title.trim(), description: desc.trim() || null, subject, level, pdf_path, created_by: user?.id,
    });
    setBusy(false);
    if (error) return toast.error(error.message);
    toast.success(isAr ? "تمت الإضافة" : "Cours ajouté");
    reset(); onClose(); onSaved();
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        onClick={e => e.stopPropagation()}
        className="w-full max-w-lg bg-card rounded-2xl border border-border shadow-2xl p-6 space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-foreground">{isAr ? "درس جديد" : "Nouveau cours"}</h2>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
        </div>
        <input value={title} onChange={e => setTitle(e.target.value)} placeholder={isAr ? "العنوان" : "Titre"}
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
        <textarea value={desc} onChange={e => setDesc(e.target.value)} placeholder={isAr ? "وصف (اختياري)" : "Description (optionnel)"}
          rows={3}
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
        <div className="grid grid-cols-2 gap-3">
          <select value={subject} onChange={e => setSubject(e.target.value as Subject)}
            className="px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm">
            {SUBJECTS.map(s => <option key={s.value} value={s.value}>{s.icon} {isAr ? s.ar : s.fr}</option>)}
          </select>
          <select value={level} onChange={e => setLevel(e.target.value as Level)}
            className="px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm">
            {LEVELS.map(l => <option key={l.value} value={l.value}>{isAr ? l.ar : l.fr}</option>)}
          </select>
        </div>
        <label className="flex items-center gap-3 p-3 rounded-lg border-2 border-dashed border-border hover:border-primary/40 cursor-pointer transition-colors">
          <Upload className="w-5 h-5 text-primary" />
          <span className="text-sm text-muted-foreground flex-1 truncate">
            {file ? file.name : (isAr ? "اختر ملف PDF (اختياري)" : "Choisir un fichier PDF (optionnel)")}
          </span>
          <input type="file" accept="application/pdf,.pdf" onChange={e => setFile(e.target.files?.[0] ?? null)} className="hidden" />
        </label>
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-border text-sm font-medium hover:bg-muted">
            {isAr ? "إلغاء" : "Annuler"}
          </button>
          <button onClick={save} disabled={busy}
            className="px-4 py-2 rounded-lg gradient-primary text-primary-foreground text-sm font-medium flex items-center gap-2 disabled:opacity-60">
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {isAr ? "حفظ" : "Enregistrer"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* TESTS                                                               */
/* ------------------------------------------------------------------ */
function TestsManager({ lang }: { lang: Lang }) {
  const isAr = lang === "ar";
  const [tests, setTests] = useState<TestRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creatingOpen, setCreatingOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    const { data, error } = await supabase.from("level_tests").select("*").order("created_at", { ascending: false });
    if (error) toast.error(error.message);
    setTests((data as TestRow[]) ?? []);
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const remove = async (id: string) => {
    if (!confirm(isAr ? "حذف هذا الاختبار؟" : "Supprimer ce test ?")) return;
    const { error } = await supabase.from("level_tests").delete().eq("id", id);
    if (error) return toast.error(error.message);
    load();
  };

  if (editingId) {
    return <QuestionsEditor testId={editingId} lang={lang} onBack={() => { setEditingId(null); load(); }} />;
  }

  const subjectLabel = (v: string) => SUBJECTS.find(s => s.value === v) ?? { fr: v, ar: v, icon: "📘" };

  return (
    <div>
      <div className="flex justify-end mb-6">
        <button onClick={() => setCreatingOpen(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg gradient-primary text-primary-foreground font-medium text-sm shadow-soft">
          <Plus className="w-4 h-4" />
          {isAr ? "اختبار جديد" : "Nouveau test"}
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : tests.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground glass-card">
          {isAr ? "لا توجد اختبارات بعد." : "Aucun test pour le moment."}
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {tests.map(tst => {
            const sub = subjectLabel(tst.subject);
            return (
              <div key={tst.id} className="glass-card p-5 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-2xl">{sub.icon}</span>
                    <div className="min-w-0">
                      <p className="font-semibold text-foreground truncate">{tst.title}</p>
                      <p className="text-xs text-muted-foreground">{isAr ? sub.ar : sub.fr} • {tst.level}ème</p>
                    </div>
                  </div>
                  <button onClick={() => remove(tst.id)} className="p-1.5 rounded-md hover:bg-destructive/10 text-destructive">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                <button onClick={() => setEditingId(tst.id)}
                  className="text-xs font-medium text-primary hover:underline self-start">
                  {isAr ? "تعديل الأسئلة ←" : "Modifier les questions →"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <NewTestDialog
        open={creatingOpen}
        onClose={() => setCreatingOpen(false)}
        onCreated={(id) => { setCreatingOpen(false); load(); setEditingId(id); }}
        lang={lang}
      />
    </div>
  );
}

function NewTestDialog({ open, onClose, onCreated, lang }: { open: boolean; onClose: () => void; onCreated: (id: string) => void; lang: Lang }) {
  const isAr = lang === "ar";
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState<Subject>("math");
  const [level, setLevel] = useState<Level>("7");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!title.trim()) return toast.error(isAr ? "العنوان مطلوب" : "Titre requis");
    setBusy(true);
    const { data: { user } } = await supabase.auth.getUser();
    const { data, error } = await supabase.from("level_tests")
      .insert({ title: title.trim(), subject, level, created_by: user?.id })
      .select("id").single();
    setBusy(false);
    if (error) return toast.error(error.message);
    setTitle("");
    onCreated(data.id as string);
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }}
        onClick={e => e.stopPropagation()}
        className="w-full max-w-md bg-card rounded-2xl border border-border shadow-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">{isAr ? "اختبار جديد" : "Nouveau test"}</h2>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
        </div>
        <input value={title} onChange={e => setTitle(e.target.value)} placeholder={isAr ? "عنوان الاختبار" : "Titre du test"}
          className="w-full px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
        <div className="grid grid-cols-2 gap-3">
          <select value={subject} onChange={e => setSubject(e.target.value as Subject)}
            className="px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm">
            {SUBJECTS.map(s => <option key={s.value} value={s.value}>{s.icon} {isAr ? s.ar : s.fr}</option>)}
          </select>
          <select value={level} onChange={e => setLevel(e.target.value as Level)}
            className="px-3 py-2.5 rounded-lg border border-border bg-muted/30 text-sm">
            {LEVELS.map(l => <option key={l.value} value={l.value}>{isAr ? l.ar : l.fr}</option>)}
          </select>
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-border text-sm font-medium hover:bg-muted">
            {isAr ? "إلغاء" : "Annuler"}
          </button>
          <button onClick={create} disabled={busy}
            className="px-4 py-2 rounded-lg gradient-primary text-primary-foreground text-sm font-medium flex items-center gap-2 disabled:opacity-60">
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            {isAr ? "إنشاء" : "Créer"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function QuestionsEditor({ testId, lang, onBack }: { testId: string; lang: Lang; onBack: () => void }) {
  const isAr = lang === "ar";
  const [questions, setQuestions] = useState<Question[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    const { data, error } = await supabase
      .from("test_questions").select("*").eq("test_id", testId).order("position");
    if (error) toast.error(error.message);
    setQuestions(
      (data ?? []).map((q: any) => ({
        id: q.id, question: q.question, choices: q.choices, correct_index: q.correct_index, position: q.position,
      }))
    );
    setLoading(false);
  };
  useEffect(() => { load(); }, [testId]);

  const add = () => setQuestions(q => [...q, { question: "", choices: ["", "", "", ""], correct_index: 0, position: q.length }]);

  const update = (i: number, patch: Partial<Question>) =>
    setQuestions(qs => qs.map((q, idx) => idx === i ? { ...q, ...patch } : q));

  const updateChoice = (i: number, ci: number, value: string) =>
    setQuestions(qs => qs.map((q, idx) => idx === i ? { ...q, choices: q.choices.map((c, j) => j === ci ? value : c) } : q));

  const removeQ = (i: number) => setQuestions(qs => qs.filter((_, idx) => idx !== i));

  const save = async () => {
    for (const q of questions) {
      if (!q.question.trim()) return toast.error(isAr ? "كل الأسئلة يجب أن تكون مكتوبة" : "Toutes les questions doivent être remplies");
      if (q.choices.some(c => !c.trim())) return toast.error(isAr ? "كل الاختيارات مطلوبة" : "Tous les choix sont requis");
    }
    setSaving(true);
    // Replace strategy: delete then insert
    const { error: delErr } = await supabase.from("test_questions").delete().eq("test_id", testId);
    if (delErr) { setSaving(false); return toast.error(delErr.message); }
    if (questions.length) {
      const payload = questions.map((q, idx) => ({
        test_id: testId, question: q.question.trim(),
        choices: q.choices.map(c => c.trim()), correct_index: q.correct_index, position: idx,
      }));
      const { error } = await supabase.from("test_questions").insert(payload);
      if (error) { setSaving(false); return toast.error(error.message); }
    }
    setSaving(false);
    toast.success(isAr ? "تم الحفظ" : "Enregistré");
    onBack();
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <button onClick={onBack} className="flex items-center gap-2 text-sm text-muted-foreground hover:text-primary">
          <ArrowLeft className={`w-4 h-4 ${isAr ? "rotate-180" : ""}`} />
          {isAr ? "رجوع" : "Retour"}
        </button>
        <div className="flex gap-2">
          <button onClick={add} className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-sm font-medium hover:bg-muted">
            <Plus className="w-4 h-4" />
            {isAr ? "سؤال" : "Question"}
          </button>
          <button onClick={save} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg gradient-primary text-primary-foreground text-sm font-medium disabled:opacity-60">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {isAr ? "حفظ الكل" : "Enregistrer"}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : questions.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground glass-card">
          {isAr ? "أضف سؤالك الأول." : "Ajoute ta première question."}
        </div>
      ) : (
        <div className="space-y-4">
          {questions.map((q, i) => (
            <div key={i} className="glass-card p-5 space-y-3">
              <div className="flex items-start gap-3">
                <span className="w-7 h-7 rounded-full bg-primary/10 text-primary text-xs font-bold flex items-center justify-center shrink-0 mt-1">
                  {i + 1}
                </span>
                <textarea
                  value={q.question}
                  onChange={e => update(i, { question: e.target.value })}
                  placeholder={isAr ? "نص السؤال" : "Énoncé de la question"}
                  rows={2}
                  className="flex-1 px-3 py-2 rounded-lg border border-border bg-muted/30 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                />
                <button onClick={() => removeQ(i)} className="p-1.5 rounded-md hover:bg-destructive/10 text-destructive">
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="grid sm:grid-cols-2 gap-2 pl-10">
                {q.choices.map((c, ci) => (
                  <label key={ci}
                    className={`flex items-center gap-2 p-2 rounded-lg border-2 cursor-pointer transition-colors ${
                      q.correct_index === ci ? "border-primary bg-primary/5" : "border-border hover:border-primary/30"
                    }`}>
                    <input
                      type="radio"
                      checked={q.correct_index === ci}
                      onChange={() => update(i, { correct_index: ci })}
                      className="accent-primary"
                    />
                    <span className="text-xs font-bold text-muted-foreground w-4">{String.fromCharCode(65 + ci)}</span>
                    <input
                      value={c}
                      onChange={e => updateChoice(i, ci, e.target.value)}
                      placeholder={isAr ? `اختيار ${ci + 1}` : `Choix ${ci + 1}`}
                      className="flex-1 bg-transparent text-sm focus:outline-none"
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}