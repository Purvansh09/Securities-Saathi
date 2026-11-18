import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";
import { useMe } from "../lib/queries";

export function Layout() {
  const { session, signOut } = useAuth();
  const me = useMe();
  const who = me.data?.full_name || session?.user.email;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            Securities Saathi
          </NavLink>
          <nav className="nav">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/holdings">Holdings</NavLink>
            <NavLink to="/transactions">Transactions</NavLink>
            <NavLink to="/tax">Tax</NavLink>
            <NavLink to="/import">Import</NavLink>
          </nav>
          <div className="user">
            <span className="user-name" title={session?.user.email}>
              {who}
            </span>
            <button className="btn btn-ghost btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  );
}
