import { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Loader2 } from "lucide-react";
import { setLang } from "@/lib/i18n";
import { getSubject } from "@/lib/subjects";
import { getUserLang, getUserPref, hasCompletedOnboarding } from "@/lib/userPrefs";

interface Props {
  children: JSX.Element;
  requireRole?: "admin" | "student";
}

export default function ProtectedRoute({ children, requireRole }: Props) {
  const { user, role, loading } = useAuth();
  const location = useLocation();
  const onboarded = hasCompletedOnboarding(user?.id);
  const assessmentDone = getUserPref(user?.id, "assessment-done") === "true";
  const subject = getSubject(user?.id);

  useEffect(() => {
    const savedLang = getUserLang(user?.id);
    if (savedLang) setLang(savedLang);
  }, [user?.id]);

  if (loading || (user && requireRole && role === null)) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/auth" state={{ from: location.pathname }} replace />;
  }

  if (requireRole && role !== requireRole) {
    return <Navigate to={role === "admin" ? "/admin" : "/dashboard"} replace />;
  }

  if (requireRole === "student" && !onboarded && location.pathname !== "/onboarding") {
    return <Navigate to="/onboarding" replace />;
  }

  if (requireRole === "student" && onboarded && location.pathname === "/onboarding") {
    return <Navigate to={subject ? "/dashboard" : "/subjects?next=dashboard"} replace />;
  }

  if (
    requireRole === "student" &&
    onboarded &&
    assessmentDone &&
    !subject &&
    location.pathname !== "/subjects"
  ) {
    return <Navigate to="/subjects?next=dashboard" replace />;
  }

  return children;
}
