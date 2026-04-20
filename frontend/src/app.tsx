import { Link, Route, Routes } from "react-router-dom";
import HomeRoute from "./routes/home";
import PortfolioBuilderRoute from "./routes/portfolio-builder";
import SegmentIntelligenceRoute from "./routes/segment-intelligence";
import LeadQueueRoute from "./routes/lead-queue";
import Borrower360Route from "./routes/borrower-360";
import OfferOrchestratorRoute from "./routes/offer-orchestrator";
import AskGenieRoute from "./routes/ask-genie";
import AdminConfigRoute from "./routes/admin-config";

const links = [
  ["/", "Home"],
  ["/portfolio-builder", "Portfolio Builder"],
  ["/segment-intelligence", "Segment Intelligence"],
  ["/lead-queue", "Lead Queue"],
  ["/borrower-360", "Borrower 360"],
  ["/offer-orchestrator", "Offer Orchestrator"],
  ["/ask-genie", "Ask Genie"],
  ["/admin-config", "Admin Config"],
] as const;

export default function App() {
  return (
    <main style={{ fontFamily: "sans-serif", padding: 16 }}>
      <h1>Mortgage Intelligence Platform</h1>
      <nav style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        {links.map(([to, label]) => (
          <Link key={to} to={to}>
            {label}
          </Link>
        ))}
      </nav>
      <Routes>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/portfolio-builder" element={<PortfolioBuilderRoute />} />
        <Route path="/segment-intelligence" element={<SegmentIntelligenceRoute />} />
        <Route path="/lead-queue" element={<LeadQueueRoute />} />
        <Route path="/borrower-360" element={<Borrower360Route />} />
        <Route path="/offer-orchestrator" element={<OfferOrchestratorRoute />} />
        <Route path="/ask-genie" element={<AskGenieRoute />} />
        <Route path="/admin-config" element={<AdminConfigRoute />} />
      </Routes>
    </main>
  );
}
