export type FeedbackAttachment = { id: string; filename: string; mime_type: string; size: number };
export type FeedbackStatus = "pending" | "processing" | "waiting_user" | "resolved";
export type FeedbackCategory = "bug" | "suggestion" | "question";
export type FeedbackThread = {
  id: string; owner_id: string; owner_name: string; title: string; category: FeedbackCategory;
  status: FeedbackStatus; assigned_to: string | null; assigned_name: string | null;
  source_page: string; task_id: string; app_version: string; preview: string;
  last_seq: number; read_seq: number; unread: number; other_read_seq: number;
  created_at: string; updated_at: string;
};
export type FeedbackMessage = {
  id: string; thread_id: string; seq: number; sender_id: string; sender_name: string;
  sender_role: "admin" | "user"; client_id: string; content: string; internal: boolean;
  kind: "message" | "status"; created_at: string; attachments: FeedbackAttachment[];
};
export type MessageDraft = { client_id: string; content: string; attachment_ids: string[]; internal: boolean };
export type ThreadDraft = MessageDraft & { title?: string; category: FeedbackCategory; source_page: string; task_id: string; app_version: string };
export type FeedbackSettings = { notify_enabled: boolean; site_url: string; pending_events: number; webhooks: { id: string; name: string; enabled: boolean }[] };
export const statusLabels: Record<FeedbackStatus, string> = { pending: "待处理", processing: "处理中", waiting_user: "待你补充", resolved: "已解决" };
export const categoryLabels: Record<FeedbackCategory, string> = { bug: "功能异常", suggestion: "需求建议", question: "使用咨询" };
