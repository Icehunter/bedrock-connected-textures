import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import Players from "./pages/Players";
import PackMakers from "./pages/PackMakers";
import Developers from "./pages/Developers";
import Docs from "./pages/Docs";

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="players" element={<Players />} />
          <Route path="pack-makers" element={<PackMakers />} />
          <Route path="developers" element={<Developers />} />
          <Route path="docs" element={<Docs />} />
          <Route path="docs/:slug" element={<Docs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
