import { useCallback, useEffect, useState } from "react";
import {
  api,
  clearToken,
  demoLogin,
  getToken,
  isDemo,
  setToken,
  setUnauthorizedHandler,
  type AdminMe,
} from "./api";
import Login from "./Login";
import Shell from "./Shell";

/** Auth state machine: probing (token exists / demo bootstrapping) → in | out.
 *  At /demo we auto-authenticate via /api/demo-login and never show Login. */
export default function App() {
  const [me, setMe] = useState<AdminMe | null>(null);
  const [probing, setProbing] = useState<boolean>(!!getToken() || isDemo);

  const logout = useCallback(() => {
    clearToken();
    setMe(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));

    const existing = getToken();
    if (existing) {
      api<AdminMe>("/me")
        .then(setMe)
        .catch(() => clearToken())
        .finally(() => setProbing(false));
      return;
    }

    // Demo: bootstrap a read-only session instead of showing the login form.
    if (isDemo) {
      demoLogin()
        .then((r) => {
          setToken(r.access_token);
          setMe(r.admin);
        })
        .catch(() => clearToken()) // fall through to the (rare) login fallback
        .finally(() => setProbing(false));
    }
  }, []);

  if (probing) {
    return (
      <div className="flex min-h-screen items-center justify-center font-mono text-sm text-zinc-400">
        Checking session…
      </div>
    );
  }
  if (!me) return <Login onLoggedIn={setMe} />;
  return <Shell me={me} onLogout={logout} />;
}
