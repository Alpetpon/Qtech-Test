import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Outlet, Route, Routes } from "react-router";

import { api } from "./api/client";
import "./styles.css";

function BackendStatus() {
  const [status, setStatus] = useState("проверяем…");

  useEffect(() => {
    api.GET("/api/health").then(
      ({ data, response }) =>
        setStatus(data ? `${data.status}, db: ${data.db}` : `ошибка ${response.status}`),
      () => setStatus("недоступен"),
    );
  }, []);

  return <p>Бэкенд: {status}</p>;
}

function Layout() {
  return (
    <>
      <header>
        <Link to="/" className="brand">
          Qtech
        </Link>
        <BackendStatus />
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}

function NotFound() {
  return (
    <p>
      Страница не найдена. <Link to="/">На главную</Link>
    </p>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<h1>Рассылка поставщикам и разбор предложений</h1>} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
