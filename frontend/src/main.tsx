import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

// Earlier builds stored nonfunctional API key secrets in browser storage.
// Remove them before any authenticated UI mounts.
try {
  localStorage.removeItem('smt_api_keys_local_secrets');
  localStorage.removeItem('smt_api_keys_local_list');
} catch {
  // Storage may be disabled in private browsing.
}

createRoot(document.getElementById("root")!).render(<App />);
