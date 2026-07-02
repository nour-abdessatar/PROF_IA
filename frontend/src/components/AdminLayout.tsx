import { NavLink, useNavigate } from "react-router-dom";
import { ReactNode } from "react";
import { Upload, Images, BookOpen, Users, LogOut, ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

const navItems = [
  { to: "/admin/upload", label: "Upload", icon: Upload },
  { to: "/admin/review-images", label: "Review images", icon: Images },
  { to: "/admin/courses", label: "Courses", icon: BookOpen },
  { to: "/admin/users", label: "Users", icon: Users },
];

export default function AdminLayout({ children, title }: { children: ReactNode; title?: string }) {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-dvh bg-gradient-to-br from-background via-background to-secondary/40 flex">
      <aside className="w-64 shrink-0 border-r border-sidebar-border/80 bg-sidebar/95 backdrop-blur-xl flex flex-col">
        <div className="h-16 flex items-center gap-3 px-5 border-b border-sidebar-border/80">
          <div className="w-9 h-9 rounded-xl gradient-primary flex items-center justify-center shadow-glow">
            <ShieldCheck className="w-4 h-4 text-primary-foreground" />
          </div>
          <div>
            <p className="text-sm font-display font-bold leading-tight tracking-tight">Prof IA</p>
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground leading-tight font-medium">Admin</p>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-0.5">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `group relative flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-1 rounded-r-full bg-primary" />
                  )}
                  <Icon className={`w-4 h-4 transition-transform ${isActive ? "scale-110" : "group-hover:scale-110"}`} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-sidebar-border/80 space-y-1">
          <div className="px-3 py-2 text-xs text-muted-foreground truncate rounded-lg bg-muted/40">
            {user?.email}
          </div>
          <button
            onClick={async () => { await signOut(); navigate("/auth"); }}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
          >
            <LogOut className="w-4 h-4" /> Logout
          </button>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-x-auto">
        <header className="h-16 border-b border-border/70 bg-card/60 backdrop-blur-xl flex items-center px-8 sticky top-0 z-10">
          <h1 className="text-xl font-display font-semibold tracking-tight text-foreground">{title}</h1>
        </header>
        <div className="p-8 animate-fade-in">{children}</div>
      </main>
    </div>
  );
}
