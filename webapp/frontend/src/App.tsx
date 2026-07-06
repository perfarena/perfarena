import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import Rankings from "./pages/Rankings";
import Problems from "./pages/Problems";
import ProblemDetail from "./pages/ProblemDetail";
import Submissions from "./pages/Submissions";
import SubmissionDetail from "./pages/SubmissionDetail";
import ModelDetail from "./pages/ModelDetail";
import SubmitterDetail from "./pages/SubmitterDetail";
import LanguageDetail from "./pages/LanguageDetail";
import Languages from "./pages/Languages";
import Register from "./pages/Register";
import Login from "./pages/Login";
import Admin from "./pages/Admin";
import AdminExport from "./pages/AdminExport";
import ApiKey from "./pages/ApiKey";
import MyByLanguage from "./pages/MyByLanguage";
import Compare from "./pages/Compare";
import CostEstimator from "./pages/CostEstimator";
import LeetcodeAccounts from "./pages/LeetcodeAccounts";
// The cross-language scatter is the "Correctness vs Performance" tab inside
// Compare (Compare imports it internally); the /analysis route below
// redirects there for deep links pointing at the old URL. Compare itself
// also accepts the legacy ?tab=analysis query as an alias.
import Catalog from "./pages/Catalog";
import Competitions from "./pages/Competitions";
import CompetitionDetail from "./pages/CompetitionDetail";
import Harnesses from "./pages/Harnesses";
import HarnessDetail from "./pages/HarnessDetail";
import Prompts from "./pages/Prompts";
import Blog from "./pages/Blog";
import BlogPost from "./pages/BlogPost";
import SubmitProgress from "./pages/SubmitProgress";
import AttemptResults from "./pages/AttemptResults";
import Me from "./pages/Me";
import About from "./pages/About";
import Snapshots from "./pages/Snapshots";
import SnapshotDetail from "./pages/SnapshotDetail";
import { getApiKey } from "./lib/api";

function Gated({ children }: { children: React.ReactNode }) {
  return getApiKey() ? <>{children}</> : <Navigate to="/register" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="rankings" element={<Rankings />} />
        <Route path="compare" element={<Compare />} />
        <Route path="cost-estimator" element={<CostEstimator />} />
        <Route path="admin/leetcode-accounts" element={<LeetcodeAccounts />} />
        <Route path="analysis" element={<Navigate to="/compare?tab=perf-vs-correctness" replace />} />
        {/* "Catalog" was renamed to "Models"; keep the old path as a redirect. */}
        <Route path="models" element={<Catalog />} />
        <Route path="catalog" element={<Navigate to="/models" replace />} />
        <Route path="prompts" element={<Prompts />} />
        <Route path="prompts/:id" element={<Prompts />} />
        <Route path="competitions" element={<Competitions />} />
        <Route path="competitions/:slug" element={<CompetitionDetail />} />
        <Route path="harnesses" element={<Harnesses />} />
        <Route path="harnesses/:slug" element={<HarnessDetail />} />
        <Route path="problems" element={<Problems />} />
        <Route path="problems/:slug" element={<ProblemDetail />} />
        <Route path="submissions" element={<Submissions />} />
        <Route path="submissions/:id" element={<SubmissionDetail />} />
        <Route path="models/:name" element={<ModelDetail />} />
        <Route path="submitters/:slug" element={<SubmitterDetail />} />
        <Route path="languages" element={<Languages />} />
        <Route path="languages/:name" element={<LanguageDetail />} />
        <Route path="snapshots" element={<Snapshots />} />
        <Route path="snapshots/:id" element={<SnapshotDetail />} />
        <Route path="blog" element={<Blog />} />
        <Route path="blog/:slug" element={<BlogPost />} />
        <Route path="about" element={<About />} />
        <Route path="register" element={<Register />} />
        <Route path="login" element={<Login />} />
        {/* /submit merged into /submissions (table on top, submit flow below). */}
        <Route path="submit" element={<Navigate to="/submissions" replace />} />
        <Route path="submit/:id" element={<Gated><SubmitProgress /></Gated>} />
        <Route path="submit/:id/attempts" element={<Gated><AttemptResults /></Gated>} />
        <Route path="me" element={<Gated><Me /></Gated>} />
        <Route path="me/api-key" element={<Gated><ApiKey /></Gated>} />
        <Route path="me/by-language" element={<Gated><MyByLanguage /></Gated>} />
        <Route path="admin" element={<Gated><Admin /></Gated>} />
        <Route path="admin/export" element={<Gated><AdminExport /></Gated>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
