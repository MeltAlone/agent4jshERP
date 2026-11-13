import { useQuery } from "@tanstack/react-query";
import { SessionConnection } from "./components/SessionConnection";
import { EventVerification } from "./components/EventVerification";

type ServiceStatus = { agent_available: boolean };
async function loadStatus(): Promise<ServiceStatus> {
  const response = await fetch("/assistant/api/v1/status", { credentials: "same-origin" });
  if (!response.ok) throw new Error("暂时无法连接助手服务");
  return response.json() as Promise<ServiceStatus>;
}
export function App() {
  const status = useQuery({ queryKey: ["service-status"], queryFn: loadStatus });
  const debug = import.meta.env.DEV && new URLSearchParams(window.location.search).has("debug");
  return <main className="assistant-app">
    <SessionConnection agentAvailable={status.data?.agent_available} serviceError={status.isError} />
    {debug && <details className="shrink-0 border-t border-border p-3"><summary>开发验证工具</summary><EventVerification /></details>}
  </main>;
}
