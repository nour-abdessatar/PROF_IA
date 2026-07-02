import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Loader2, Trophy } from "lucide-react";
import { getResults } from "@/lib/api";
import { t, useLang } from "@/lib/i18n";

export default function Results() {
  const { session_id } = useParams<{ session_id: string }>();
  const navigate = useNavigate();
  const lang = useLang();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!session_id) return;
    getResults(session_id)
      .then((d) => setData(d))
      .catch((e) => setError(e?.message ?? String(e)))
      .finally(() => setLoading(false));
  }, [session_id]);

  return (
    <div className="min-h-screen bg-background px-6 py-12" dir={lang === "ar" ? "rtl" : "ltr"}>
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gradient">{t("results_title", lang)}</h1>
          <button
            onClick={() => navigate("/dashboard")}
            className="text-sm text-muted-foreground hover:text-primary"
          >
            ← {t("back", lang)}
          </button>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" /> …
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {data && (
          <div className="glass-card p-8 space-y-6">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl gradient-primary flex items-center justify-center">
                <Trophy className="w-7 h-7 text-primary-foreground" />
              </div>
              <div>
                <p className="text-sm text-muted-foreground">{t("score", lang)}</p>
                <p className="text-3xl font-bold text-foreground">
                  {data.score ?? "—"}
                  {typeof data.total !== "undefined" ? ` / ${data.total}` : ""}
                </p>
              </div>
            </div>

            {data.feedback && (
              <div className="space-y-1">
                <p className="text-sm font-semibold text-foreground">{t("feedback", lang)}</p>
                <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                  {typeof data.feedback === "string"
                    ? data.feedback
                    : JSON.stringify(data.feedback, null, 2)}
                </p>
              </div>
            )}

            {Array.isArray(data.details) && (
              <ul className="space-y-2">
                {data.details.map((d: any, i: number) => (
                  <li key={i} className="text-sm bg-muted/40 rounded-lg p-3">
                    <pre className="whitespace-pre-wrap">{JSON.stringify(d, null, 2)}</pre>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}