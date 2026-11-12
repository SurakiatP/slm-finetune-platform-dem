import { lazy } from "react";
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

// Dashboard pages load on demand to keep the initial bundle small.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Projects = lazy(() => import("./pages/Projects"));
const ProjectDetail = lazy(() => import("./pages/ProjectDetail"));
const Models = lazy(() => import("./pages/Models"));
const NewProject = lazy(() => import("./pages/NewProject"));
const TrainingMonitor = lazy(() => import("./pages/TrainingMonitor"));
const Playground = lazy(() => import("./pages/Playground"));
const ModelDetail = lazy(() => import("./pages/ModelDetail"));
const ModelComparison = lazy(() => import("./pages/ModelComparison"));
const DatasetInsights = lazy(() => import("./pages/DatasetInsights"));
const Datasets = lazy(() => import("./pages/Datasets"));
const Evaluations = lazy(() => import("./pages/Evaluations"));
const Usage = lazy(() => import("./pages/Usage"));
const Settings = lazy(() => import("./pages/Settings"));
const ApiKeysPage = lazy(() => import("./pages/ApiKeys"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Deployment = lazy(() => import("./pages/Deployment"));
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const Templates = lazy(() => import("./pages/Templates"));

const queryClient = new QueryClient();

const App = () => (
  <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
    <LanguageProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <AuthProvider>
            <BrowserRouter>
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
                  <Route path="/api-keys" element={<ApiKeysPage />} />
                </Route>
                <Route path="*" element={<NotFound />} />
              </Routes>
            </BrowserRouter>
          </AuthProvider>
        </TooltipProvider>
      </QueryClientProvider>
    </LanguageProvider>
  </ThemeProvider>
);

export default App;
