import { Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/ui";
import { Gallery } from "./pages/Gallery";
import { Logs } from "./pages/Logs";
import { ModelDetail } from "./pages/ModelDetail";
import { Models } from "./pages/Models";
import { Overview } from "./pages/Overview";
import { Settings } from "./pages/Settings";
import { Studio } from "./pages/Studio";

export default function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/models" element={<Models />} />
          <Route path="/models/:id" element={<ModelDetail />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/studio/:id" element={<Studio />} />
          <Route path="/gallery" element={<Gallery />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Overview />} />
        </Routes>
      </main>
      <Toasts />
    </div>
  );
}
