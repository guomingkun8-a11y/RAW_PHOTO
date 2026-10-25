import { httpRequest, request } from "@/lib/request";
import type { FeedbackAttachment, FeedbackMessage, FeedbackSettings, FeedbackThread, MessageDraft, ThreadDraft } from "./types";

const root = "/api/feedback";
export const feedbackApi = {
  threads: (params: URLSearchParams) => httpRequest<{ items: FeedbackThread[]; total: number }>(`${root}/threads?${params}`),
  thread: (id: string) => httpRequest<FeedbackThread>(`${root}/threads/${encodeURIComponent(id)}`),
  create: (body: ThreadDraft) => httpRequest<FeedbackThread>(`${root}/threads`, { method: "POST", body }),
  messages: (id: string, cursor = "") => httpRequest<{ items: FeedbackMessage[]; has_more: boolean; thread: FeedbackThread }>(`${root}/threads/${encodeURIComponent(id)}/messages${cursor ? `?${cursor}` : ""}`),
  send: (id: string, body: MessageDraft) => httpRequest<FeedbackMessage>(`${root}/threads/${encodeURIComponent(id)}/messages`, { method: "POST", body, timeout: 15000 }),
  read: (id: string, seq: number) => httpRequest<{ read_seq: number }>(`${root}/threads/${encodeURIComponent(id)}/read`, { method: "POST", body: { seq } }),
  update: (id: string, body: object) => httpRequest<FeedbackThread>(`${root}/threads/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  unread: () => httpRequest<{ unread: number }>(`${root}/unread`),
  settings: () => httpRequest<FeedbackSettings>(`${root}/settings`),
  saveSettings: (body: Pick<FeedbackSettings, "notify_enabled" | "site_url">) => httpRequest<FeedbackSettings>(`${root}/settings`, { method: "PUT", body }),
  async upload(file: File) {
    const body = new FormData();
    body.append("file", file);
    return httpRequest<FeedbackAttachment>(`${root}/attachments`, { method: "POST", body, timeout: 45000 });
  },
  async image(id: string) {
    const response = await request.get<Blob>(`${root}/attachments/${encodeURIComponent(id)}/content`, { responseType: "blob" });
    return URL.createObjectURL(response.data);
  },
};
