import { useEffect, useState } from "react";
import { Loader2, Check, X as XIcon } from "lucide-react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import AdminLayout from "@/components/AdminLayout";
import { apiGet, apiPost } from "@/lib/api";

type Decision = "KEEP" | "KEEP_TEXT" | "DROP";

interface ReviewImage {
  id: string;
  page: number;
  image_url?: string;
  image_base64?: string;
  decision: Decision;
  extracted_text?: string;
}

export default function AdminReviewImages() {
  const navigate = useNavigate();
  const [images, setImages] = useState<ReviewImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiGet<{ images: ReviewImage[] }>("/admin/review-images")
      .then((r) => setImages(r.images ?? []))
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, []);

  const update = (id: string, patch: Partial<ReviewImage>) =>
    setImages((prev) => prev.map((img) => (img.id === id ? { ...img, ...patch } : img)));

  const submit = async () => {
    setBusy(true);
    try {
      await apiPost("/admin/review-submit", {
        images: images.map((i) => ({ id: i.id, decision: i.decision, extracted_text: i.extracted_text })),
      });
      toast.success("Review submitted. Indexing started.");
      navigate("/admin/courses");
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const src = (i: ReviewImage) =>
    i.image_url ?? (i.image_base64 ? `data:image/jpeg;base64,${i.image_base64}` : "");

  return (
    <AdminLayout title="Review extracted images">
      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-6 h-6 animate-spin text-primary" /></div>
      ) : images.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground rounded-xl border border-border">
          No images to review.
        </div>
      ) : (
        <>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {images.map((img) => (
              <div key={img.id} className="rounded-xl border border-border bg-card overflow-hidden flex flex-col">
                <div className="relative bg-muted aspect-[3/4] flex items-center justify-center">
                  {src(img) ? (
                    <img src={src(img)} alt={`Page ${img.page}`} className="w-full h-full object-contain" />
                  ) : (
                    <span className="text-sm text-muted-foreground">No preview</span>
                  )}
                  <span className="absolute top-2 left-2 text-xs bg-background/80 backdrop-blur px-2 py-0.5 rounded">
                    Page {img.page}
                  </span>
                </div>
                <div className="p-3 space-y-2">
                  <div className="flex gap-1.5">
                    {(["KEEP", "KEEP_TEXT", "DROP"] as Decision[]).map((d) => (
                      <button
                        key={d}
                        onClick={() => update(img.id, { decision: d })}
                        className={`flex-1 px-2 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                          img.decision === d
                            ? d === "DROP"
                              ? "bg-destructive text-destructive-foreground border-destructive"
                              : "bg-primary text-primary-foreground border-primary"
                            : "bg-background border-border text-muted-foreground hover:bg-muted"
                        }`}
                      >
                        {d === "KEEP_TEXT" ? "Keep + Text" : d === "KEEP" ? "Keep" : "Drop"}
                      </button>
                    ))}
                  </div>
                  {img.decision === "KEEP_TEXT" && (
                    <textarea
                      value={img.extracted_text ?? ""}
                      onChange={(e) => update(img.id, { extracted_text: e.target.value })}
                      placeholder="Extracted text"
                      rows={4}
                      className="w-full px-2 py-1.5 rounded-md border border-border bg-muted/30 text-xs focus:outline-none focus:ring-2 focus:ring-primary/30"
                    />
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="sticky bottom-0 mt-8 -mx-8 px-8 py-4 bg-background/80 backdrop-blur border-t border-border flex justify-end gap-2">
            <button
              onClick={submit}
              disabled={busy}
              className="px-5 py-2.5 rounded-lg gradient-primary text-primary-foreground text-sm font-medium flex items-center gap-2 disabled:opacity-60"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              Confirm & continue
            </button>
          </div>
        </>
      )}
    </AdminLayout>
  );
}