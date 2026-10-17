import { backendConfigFromEnv, forwardToBackend } from "@/lib/backend-proxy";

// Every /api/* request from the browser is forwarded to the backend with the server-side token attached.
async function handler(request: Request): Promise<Response> {
  return forwardToBackend(request, backendConfigFromEnv());
}

export { handler as DELETE, handler as GET, handler as HEAD, handler as PATCH, handler as POST, handler as PUT };
