// Server-side forwarding of /api/* to the backend. The browser only ever talks to this Next.js server, so the
// backend's DD_API_TOKEN stays in server memory and is never inlined into the client bundle; file and report
// links (plain <a>/<iframe>, which can't carry an Authorization header) work because they are same-origin.

export type ProxyConfig = {
  backendUrl: string;
  token: string | undefined;
  fetch?: typeof fetch;
};

// Hop-by-hop headers (RFC 9110 §7.6.1) and headers this proxy owns. The browser's cookies and credentials are
// for this server, not the backend, so they are never forwarded.
const DROPPED_REQUEST_HEADERS = [
  "connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "te", "trailer",
  "host", "content-length", "authorization", "cookie",
];
const DROPPED_RESPONSE_HEADERS = [
  "connection", "keep-alive", "transfer-encoding", "upgrade", "trailer", "content-encoding", "content-length",
  "set-cookie",
];

export function backendConfigFromEnv(): ProxyConfig {
  return {
    backendUrl: process.env.API_URL ?? "http://localhost:8000",
    token: process.env.DD_API_TOKEN?.trim() || undefined,
  };
}

/** Forward `request` (whose path is /api/...) to the backend and return its response unchanged. */
export async function forwardToBackend(request: Request, config: ProxyConfig): Promise<Response> {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, config.backendUrl);

  const headers = new Headers(request.headers);
  for (const name of DROPPED_REQUEST_HEADERS) headers.delete(name);
  if (config.token) headers.set("authorization", `Bearer ${config.token}`);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await (config.fetch ?? fetch)(target, {
      method: request.method,
      headers,
      // Buffered rather than streamed: uploads are capped at 25 MiB by the backend, and a buffered body
      // needs no `duplex` option.
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "Cannot reach the backend API." }, { status: 502 });
  }

  const responseHeaders = new Headers(upstream.headers);
  for (const name of DROPPED_RESPONSE_HEADERS) responseHeaders.delete(name);
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
}
