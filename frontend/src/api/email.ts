import { api } from "./client";

export interface EmailConfig {
  imap_host: string;
  imap_port: number;
  imap_user: string;
  imap_pass: string;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_pass: string;
  check_interval: number;
  enabled: boolean;
}

export function getEmailConfig(): Promise<EmailConfig | Record<string, never>> {
  return api.get("/email/config");
}

export function saveEmailConfig(config: Partial<EmailConfig>): Promise<EmailConfig> {
  return api.post("/email/config", config);
}

export function testEmailConnection(): Promise<{ result: string }> {
  return api.post("/email/test", {});
}

