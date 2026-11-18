const API_BASE = "/api";

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(message: string, status: number, data: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

class ApiClient {
  private token: string | null = null;

  constructor() {
    this.token = localStorage.getItem("crab_token");
  }

  setToken(token: string) {
    this.token = token;
    localStorage.setItem("crab_token", token);
  }

  clearToken() {
    this.token = null;
    localStorage.removeItem("crab_token");
  }

  getToken(): string | null {
    return this.token;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const isFormData = options.body instanceof FormData;
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };
    if (!isFormData) {
      headers["Content-Type"] = "application/json";
    }
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (res.status === 401) {
      // Only reload when we actually had a token (session expired). Without a
      // token (login page) reloading would loop forever on 401 responses.
      const hadToken = !!this.token;
      this.clearToken();
      if (hadToken) {
        window.location.reload();
      }
      throw new Error("Unauthorized");
    }
    if (res.status === 204) return undefined as T;
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail || `HTTP ${res.status}`;
      throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status, data);
    }
    return data;
  }

  get<T>(path: string, params?: Record<string, string>, signal?: AbortSignal) {
    const qs = params ? `?${new URLSearchParams(params).toString()}` : "";
    return this.request<T>(`${path}${qs}`, { signal });
  }

  /** Fetch a plain-text (e.g. Markdown) response body with the same auth as request(). */
  async getText(path: string, signal?: AbortSignal): Promise<string> {
    const headers: Record<string, string> = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    const res = await fetch(`${API_BASE}${path}`, { headers, signal });
    if (res.status === 401) {
      const hadToken = !!this.token;
      this.clearToken();
      if (hadToken) {
        window.location.reload();
      }
      throw new Error("Unauthorized");
    }
    const text = await res.text();
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const data = JSON.parse(text);
        if (data && typeof data.detail === "string") detail = data.detail;
      } catch {
        if (text) detail = text;
      }
      throw new ApiError(detail, res.status, text);
    }
    return text;
  }

  post<T>(path: string, body: unknown) {
    if (body instanceof FormData) {
      return this.request<T>(path, { method: "POST", body });
    }
    return this.request<T>(path, { method: "POST", body: JSON.stringify(body) });
  }

  patch<T>(path: string, body: unknown) {
    return this.request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
  }

  put<T>(path: string, body: unknown) {
    return this.request<T>(path, { method: "PUT", body: JSON.stringify(body) });
  }

  del<T>(path: string, body?: unknown) {
    return this.request<T>(path, {
      method: "DELETE",
      body: body ? JSON.stringify(body) : undefined,
    });
  }
}

export const api = new ApiClient();