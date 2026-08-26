import { BrowserRouter, Routes, Route } from "react-router-dom";
import Shell from "./components/Shell";
import Overview from "./pages/Overview";
import AtRisk from "./pages/AtRisk";
import Queue from "./pages/Queue";
import Payments from "./pages/Payments";
import PaymentDetail from "./pages/PaymentDetail";
import Decisions from "./pages/Decisions";
import Audit from "./pages/Audit";
import Evaluation from "./pages/Evaluation";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Overview />} />
          <Route path="at-risk" element={<AtRisk />} />
          <Route path="queue" element={<Queue />} />
          <Route path="payments" element={<Payments />} />
          <Route path="payments/:id" element={<PaymentDetail />} />
          <Route path="decisions" element={<Decisions />} />
          <Route path="audit" element={<Audit />} />
          <Route path="evaluation" element={<Evaluation />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
