import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LanguageProvider } from "@/i18n/LanguageContext";
import { AuthProvider } from "@/contexts/AuthContext";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { LegacyProjectRoute } from "@/components/project/LegacyProjectRoute";
import Index from "./pages/Index";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import MfaChallenge from "./pages/MfaChallenge";
import NotFound from "./pages/NotFound";
import DashboardLayout from "./components/dashboard/DashboardLayout";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import Models from "./pages/Models";
import NewProject from "./pages/NewProject";
import TrainingMonitor from "./pages/TrainingMonitor";
import Playground from "./pages/Playground";
import ModelDetail from "./pages/ModelDetail";
import ModelComparison from "./pages/ModelComparison";
import DatasetInsights from "./pages/DatasetInsights";
import Datasets from "./pages/Datasets";
import Evaluations from "./pages/Evaluations";
import Usage from "./pages/Usage";
import Settings from "./pages/Settings";
import Analytics from "./pages/Analytics";
import Deployment from "./pages/Deployment";
import Leaderboard from "./pages/Leaderboard";
import Templates from "./pages/Templates";

const queryClient = new QueryClient();

const App = () => (
  <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
    <LanguageProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <BrowserRouter>
            <AuthProvider>
              <Routes>
                <Route path="/" element={<Index />} />
                <Route path="/login" element={<Login />} />
                <Route path="/signup" element={<Signup />} />
                <Route path="/forgot-password" element={<ForgotPassword />} />
                <Route path="/reset-password" element={<ResetPassword />} />
                <Route path="/mfa" element={<MfaChallenge />} />
                <Route element={<ProtectedRoute><DashboardLayout /></ProtectedRoute>}>
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/projects" element={<Projects />} />
                  <Route path="/projects/new" element={<NewProject />} />
                  <Route path="/projects/:id" element={<LegacyProjectRoute />}>
                    <Route index element={<ProjectDetail />} />
                    <Route path="insights" element={<DatasetInsights />} />
                    <Route path="training" element={<TrainingMonitor />} />
                  </Route>
                  <Route path="/models" element={<Models />} />
                  <Route path="/models/:id" element={<ModelDetail />} />
                  <Route path="/models/compare" element={<ModelComparison />} />
                  <Route path="/datasets" element={<Datasets />} />
                  <Route path="/evaluations" element={<Evaluations />} />
                  <Route path="/usage" element={<Usage />} />
                  <Route path="/playground" element={<Playground />} />
                  <Route path="/analytics" element={<Analytics />} />
                  <Route path="/deployment" element={<Deployment />} />
                  <Route path="/leaderboard" element={<Leaderboard />} />
                  <Route path="/templates" element={<Templates />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="/api-keys" element={<Settings />} />
                </Route>
                <Route path="*" element={<NotFound />} />
              </Routes>
            </AuthProvider>
          </BrowserRouter>
        </TooltipProvider>
      </QueryClientProvider>
    </LanguageProvider>
  </ThemeProvider>
);

export default App;
