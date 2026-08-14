import { useEffect, useState } from "react";
import { fetchDbHealth, type DbHealthResponse } from "../api/client";

type ConnectionState =
  | { status: "loading" }
  | { status: "success"; data: DbHealthResponse }
  | { status: "error"; message: string };

function StatusPage() {
  const [connection, setConnection] = useState<ConnectionState>({ status: "loading" });

  useEffect(() => {
    fetchDbHealth()
      .then((data) => setConnection({ status: "success", data }))
      .catch((err) =>
        setConnection({
          status: "error",
          message: err instanceof Error ? err.message : "Unknown error",
        }),
      );
  }, []);

  return (
    <main className="status-page">
      <h1>AFL Analytics Platform</h1>
      <p className="subtitle">System status</p>

      <div className={`status-card status-card--${connection.status}`}>
        {connection.status === "loading" && <p>Checking connection to backend…</p>}

        {connection.status === "success" && (
          <>
            <p className="status-card__headline">✅ Frontend → API → Database connected</p>
            <dl>
              <dt>API status</dt>
              <dd>{connection.data.status}</dd>
              <dt>Database</dt>
              <dd>{connection.data.database}</dd>
              <dt>Rows in sports table</dt>
              <dd>{connection.data.sport_rows}</dd>
            </dl>
          </>
        )}

        {connection.status === "error" && (
          <>
            <p className="status-card__headline">❌ Could not reach backend</p>
            <p>{connection.message}</p>
            <p className="hint">Is the FastAPI server running at the configured VITE_API_BASE_URL?</p>
          </>
        )}
      </div>
    </main>
  );
}

export default StatusPage;
