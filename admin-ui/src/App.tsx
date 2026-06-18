import { useCallback, useEffect, useState } from "react";
import { api, clearToken, getToken, setUnauthorizedHandler, type AdminMe } from "./api";
import Login from "./Login";
import Shell from "./Shell";

/** Auth state machine: probing (token exists, validating) → in | out. */
export default function App() {
  const [me, setMe] = useState<AdminMe | null>(null);
  const [probing, setProbing] = useState<boolean>(!!getToken());

  const logout = useCallback(() => {
    clearToken();
    setMe(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    if (!getToken()) return;
    api<AdminMe>("/me")
      .then(setMe)
      .catch(() => clearToken())
      .finally(() => setProbing(false));
  }, []);

  if (probing) {
    return (
      <div className="flex min-h-screen items-center justify-center font-mono text-sm text-zinc-400">
        checking session…
      </div>
    );
  }
  if (!me) return <Login onLoggedIn={setMe} />;
  return <Shell me={me} onLogout={logout} />;
}
