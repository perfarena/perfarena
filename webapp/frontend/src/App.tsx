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
import Register from "./pages/Register";
import Login from "./pages/Login";
import Admin from "./pages/Admin";
import ApiKey from "./pages/ApiKey";
import MyByLanguage from "./pages/MyByLanguage";
import Compare from "./pages/Compare";
import Catalog from "./pages/Catalog";
import Prompts from "./pages/Prompts";
import Submit from "./pages/Submit";
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
        <Route path="catalog" element={<Catalog />} />
        <Route path="prompts" element={<Prompts />} />
        <Route path="problems" element={<Problems />} />
        <Route path="problems/:slug" element={<ProblemDetail />} />
        <Route path="submissions" element={<Submissions />} />
        <Route path="submissions/:id" element={<SubmissionDetail />} />
        <Route path="models/:name" element={<ModelDetail />} />
        <Route path="submitters/:slug" element={<SubmitterDetail />} />
        <Route path="languages/:name" element={<LanguageDetail />} />
        <Route path="snapshots" element={<Snapshots />} />
        <Route path="snapshots/:id" element={<SnapshotDetail />} />
        <Route path="about" element={<About />} />
        <Route path="register" element={<Register />} />
        <Route path="login" element={<Login />} />
        <Route path="submit" element={<Gated><Submit /></Gated>} />
        <Route path="submit/:id" element={<Gated><SubmitProgress /></Gated>} />
        <Route path="submit/:id/attempts" element={<Gated><AttemptResults /></Gated>} />
        <Route path="me" element={<Gated><Me /></Gated>} />
        <Route path="me/api-key" element={<Gated><ApiKey /></Gated>} />
        <Route path="me/by-language" element={<Gated><MyByLanguage /></Gated>} />
        <Route path="admin" element={<Gated><Admin /></Gated>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
