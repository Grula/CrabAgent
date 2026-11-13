// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import LoginPage from "./LoginPage";
const mocks = vi.hoisted(() => ({ login: vi.fn(), register: vi.fn(), patch: vi.fn(), setToken: vi.fn(), clearToken: vi.fn() }));
vi.mock("../api/client", () => ({ api: { patch: mocks.patch, setToken: mocks.setToken, clearToken: mocks.clearToken } }));
vi.mock("../api/auth", () => ({ login: mocks.login, register: mocks.register }));
it("signs in without syncing a locale to the backend", async () => {
 vi.clearAllMocks();
 mocks.login.mockResolvedValue({ access_token: "token" });
 (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
 const element = document.createElement("div"); document.body.append(element); const root = createRoot(element); const onLogin = vi.fn();
 await act(async () => root.render(<LoginPage onLogin={onLogin} />));
 await act(async () => element.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
 expect(mocks.setToken).toHaveBeenCalledWith("token");
 expect(onLogin).toHaveBeenCalledOnce();
 expect(mocks.patch).not.toHaveBeenCalled();
 expect(mocks.clearToken).not.toHaveBeenCalled();
 await act(async () => root.unmount()); element.remove();
});
