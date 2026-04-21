import { NavLink, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import HomeRoute from "./routes/home";
import PortfolioBuilderRoute from "./routes/portfolio-builder";
import SegmentIntelligenceRoute from "./routes/segment-intelligence";
import LeadQueueRoute from "./routes/lead-queue";
import Borrower360Route from "./routes/borrower-360";
import OfferOrchestratorRoute from "./routes/offer-orchestrator";
import AskGenieRoute from "./routes/ask-genie";
import AdminConfigRoute from "./routes/admin-config";

const nav: Array<[string, string]> = [
  ["/", "Home"],
  ["/portfolio-builder", "Portfolio"],
  ["/segment-intelligence", "Segments"],
  ["/lead-queue", "Leads"],
  ["/borrower-360", "Borrower 360"],
  ["/offer-orchestrator", "Offers"],
  ["/ask-genie", "Ask Genie"],
  ["/admin-config", "Admin"],
];

function SubNav() {
  return (
    <nav
      style={{
        display: "flex",
        gap: 8,
        padding: "12px 24px",
        borderBottom: "1px solid var(--line-1)",
        background: "var(--bg-1)",
        flexWrap: "wrap",
      }}
    >
      {nav.map(([to, label]) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          className={({ isActive }) => `button ${isActive ? "primary" : "ghost"}`}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <AppShell>
      <SubNav />
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
