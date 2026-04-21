import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { RouteNav } from './components/layout/RouteNav';
import HomeRoute from './routes/home';
import PortfolioBuilderRoute from './routes/portfolio-builder';
import SegmentIntelligenceRoute from './routes/segment-intelligence';
import LeadQueueRoute from './routes/lead-queue';
import Borrower360Route from './routes/borrower-360';
import OfferOrchestratorRoute from './routes/offer-orchestrator';
import AskGenieRoute from './routes/ask-genie';
import AdminConfigRoute from './routes/admin-config';

export default function App() {
  return (
    <AppShell>
      <RouteNav />
      <Routes>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/portfolio-builder" element={<PortfolioBuilderRoute />} />
        <Route path="/segment-intelligence" element={<SegmentIntelligenceRoute />} />
        <Route path="/lead-queue" element={<LeadQueueRoute />} />
        <Route path="/borrower-360" element={<Borrower360Route />} />
        <Route path="/borrower-360/:id" element={<Borrower360Route />} />
        <Route path="/offer-orchestrator" element={<OfferOrchestratorRoute />} />
        <Route path="/offer-orchestrator/:id" element={<OfferOrchestratorRoute />} />
        <Route path="/ask-genie" element={<AskGenieRoute />} />
        <Route path="/admin-config" element={<AdminConfigRoute />} />
      </Routes>
    </AppShell>
  );
}
