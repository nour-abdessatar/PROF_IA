import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import Landing from "./pages/Landing";
import Auth from "./pages/Auth";
import Onboarding from "./pages/Onboarding";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import Subjects from "./pages/Subjects";
import Upload from "./pages/Upload";
import Results from "./pages/Results";
import NotFound from "./pages/NotFound";
import AdminDashboard from "./pages/AdminDashboard";
import AdminUpload from "./pages/admin/Upload";
import AdminReviewImages from "./pages/admin/ReviewImages";
import AdminCourses from "./pages/admin/Courses";
import AdminUsers from "./pages/admin/Users";
import AdminUserDetail from "./pages/admin/UserDetail";
import { AuthProvider } from "./contexts/AuthContext";
import ProtectedRoute from "./components/ProtectedRoute";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <AuthProvider>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/auth" element={<Auth />} />
          <Route path="/login" element={<Auth />} />
          <Route path="/onboarding" element={<ProtectedRoute requireRole="student"><Onboarding /></ProtectedRoute>} />
          <Route path="/dashboard" element={<ProtectedRoute requireRole="student"><Dashboard /></ProtectedRoute>} />
          <Route path="/subjects" element={<ProtectedRoute requireRole="student"><Subjects /></ProtectedRoute>} />
          <Route path="/chat" element={<ProtectedRoute requireRole="student"><Chat /></ProtectedRoute>} />
          <Route path="/upload" element={<ProtectedRoute requireRole="admin"><Upload /></ProtectedRoute>} />
          <Route path="/results/:session_id" element={<ProtectedRoute requireRole="student"><Results /></ProtectedRoute>} />
          <Route path="/admin" element={<ProtectedRoute requireRole="admin"><AdminDashboard /></ProtectedRoute>} />
          <Route path="/admin/upload" element={<ProtectedRoute requireRole="admin"><AdminUpload /></ProtectedRoute>} />
          <Route path="/admin/review-images" element={<ProtectedRoute requireRole="admin"><AdminReviewImages /></ProtectedRoute>} />
          <Route path="/admin/courses" element={<ProtectedRoute requireRole="admin"><AdminCourses /></ProtectedRoute>} />
          <Route path="/admin/users" element={<ProtectedRoute requireRole="admin"><AdminUsers /></ProtectedRoute>} />
          <Route path="/admin/users/:user_id" element={<ProtectedRoute requireRole="admin"><AdminUserDetail /></ProtectedRoute>} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        </AuthProvider>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
