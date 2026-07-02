import { motion, AnimatePresence } from "framer-motion";
import { X, BookOpen, FileText, Lightbulb } from "lucide-react";
import { t, type Lang } from "@/lib/i18n";

const mockRefs = [
  {
    source: "Manuel de Mathématiques — 7ème année",
    excerpt: "Le théorème de Pythagore stipule que dans un triangle rectangle, le carré de l'hypoténuse est égal à la somme des carrés des deux autres côtés.",
    concepts: ["Triangle rectangle", "Hypoténuse", "Théorème de Pythagore"],
  },
  {
    source: "Programme officiel — Ministère de l'Éducation",
    excerpt: "Les élèves doivent pouvoir appliquer le théorème de Pythagore pour calculer des longueurs dans des situations géométriques variées.",
    concepts: ["Calcul de longueurs", "Géométrie plane"],
  },
];

export default function ReferencesPanel({ open, onClose, lang }: { open: boolean; onClose: () => void; lang: Lang }) {
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-foreground/20 backdrop-blur-sm z-40"
            onClick={onClose}
          />
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 250 }}
            className="fixed right-0 top-0 h-full w-full max-w-md bg-card z-50 shadow-2xl border-l border-border overflow-y-auto"
          >
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-semibold">{t("ref_title", lang)}</h2>
                <button onClick={onClose} className="p-2 rounded-xl hover:bg-muted transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="space-y-4">
                {mockRefs.map((ref, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.1 }}
                    className="glass-card p-4 space-y-3"
                  >
                    <div className="flex items-center gap-2 text-sm font-medium text-primary">
                      <BookOpen className="w-4 h-4" />
                      {ref.source}
                    </div>
                    <div className="flex items-start gap-2 text-sm text-muted-foreground">
                      <FileText className="w-4 h-4 mt-0.5 shrink-0" />
                      <p>{ref.excerpt}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <Lightbulb className="w-4 h-4 text-primary" />
                      {ref.concepts.map((c) => (
                        <span key={c} className="text-xs px-2 py-1 rounded-full bg-primary-light text-primary font-medium">
                          {c}
                        </span>
                      ))}
                    </div>
                  </motion.div>
                ))}
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
