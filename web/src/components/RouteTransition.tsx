import { useOutlet, useLocation } from "react-router-dom";
import "./RouteTransition.css";

/**
 * Wraps the routed `<Outlet/>` content in a restrained fade-in + gentle upward settle
 * whenever the pathname changes, so moving between tabs (Search, Your requests, Approval
 * queue, Settings, and request detail sub-routes) doesn't feel like an instant, jarring swap.
 *
 * Cadence's router is a plain `<BrowserRouter>`/`<Routes>` setup, not a data router, so it
 * unmounts the old route and mounts the new one in the same commit -- there's no seam to
 * animate an exit, only the entering content. Keying the wrapper on `location.pathname` makes
 * React treat each route as a fresh DOM node, which restarts the CSS animation below on every
 * navigation (including each distinct `/requests/:id`).
 *
 * This deliberately uses a plain CSS `animation` on a real element rather than the View
 * Transition API: `::view-transition-old/new` pseudo-elements fall outside the
 * `*, *::before, *::after` selector that styles/tokens.css's `prefers-reduced-motion: reduce`
 * block already zeroes globally, so a real element is what stays automatically covered by it.
 */
export function RouteTransition() {
  const location = useLocation();
  const outlet = useOutlet();

  return (
    <div className="route-transition" key={location.pathname}>
      {outlet}
    </div>
  );
}
