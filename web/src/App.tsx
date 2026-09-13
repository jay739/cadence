import { lazy, Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { SearchPage } from "./pages/SearchPage";
import { MyRequestsPage } from "./pages/MyRequestsPage";
import { RequestDetailPage } from "./pages/RequestDetailPage";
import { ProfilePage } from "./pages/ProfilePage";
import { HomePage } from "./pages/HomePage";

// Code-split: only an admin ever navigates here, so their JS should not ship in the bundle
// every regular listener downloads and parses on first load.
const AdminQueuePage = lazy(() =>
  import("./pages/AdminQueuePage").then((m) => ({ default: m.AdminQueuePage })),
);
const AdminSettingsPage = lazy(() =>
  import("./pages/AdminSettingsPage").then((m) => ({ default: m.AdminSettingsPage })),
);

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/requests" element={<MyRequestsPage />} />
        <Route path="/requests/:id" element={<RequestDetailPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route
          path="/admin"
          element={
            <ProtectedRoute requireAdmin>
              <Suspense fallback={<p>Loading…</p>}>
                <AdminQueuePage />
              </Suspense>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/settings"
          element={
            <ProtectedRoute requireAdmin>
              <Suspense fallback={<p>Loading…</p>}>
                <AdminSettingsPage />
              </Suspense>
            </ProtectedRoute>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/search" replace />} />
    </Routes>
  );
}

export default App;
