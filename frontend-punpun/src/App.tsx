import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LanguageProvider } from "@/i18n/LanguageContext";
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
import Evaluations from "./pages/Evaluations";
import Usage from "./pages/Usage";

const queryClient = new QueryClient();

const App = () => (
  <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
    <LanguageProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route element={<DashboardLayout />}>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/projects" element={<Projects />} />
                <Route path="/projects/new" element={<NewProject />} />
                <Route path="/projects/:id" element={<ProjectDetail />} />
                <Route path="/projects/:id/insights" element={<DatasetInsights />} />
                <Route path="/projects/:id/training" element={<TrainingMonitor />} />
                <Route path="/models" element={<Models />} />
                <Route path="/models/:id" element={<ModelDetail />} />
                <Route path="/models/compare" element={<ModelComparison />} />
                <Route path="/evaluations" element={<Evaluations />} />
                <Route path="/usage" element={<Usage />} />
                <Route path="/playground" element={<Playground />} />
              </Route>
              <Route path="*" element={<NotFound />} />
            </Routes>
          </BrowserRouter>
        </TooltipProvider>
      </QueryClientProvider>
    </LanguageProvider>
  </ThemeProvider>
);

export default App;
