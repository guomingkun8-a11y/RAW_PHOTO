import {
  Activity, AudioLines, History, Library, PanelsTopLeft, ReceiptText,
  Sparkles, Users, Video, WandSparkles,
} from "@lucide/vue";
import { ref, type Component } from "vue";

import { httpRequest } from "@/lib/request";

export type NavDefinition = {
  id: string;
  href: string;
  label: string;
  detail: string;
  icon: Component;
  adminOnly?: boolean;
  activePath?: string;
  activeQuery?: Record<string, string>;
};

export const navItems: NavDefinition[] = [
  { id: "image", href: "/image", label: "图片生成", detail: "AI 创作台", icon: WandSparkles },
  { id: "video-generation", href: "/video-generation", label: "视频生成", detail: "文生视频 / 图生视频", icon: Video },
  { id: "audio-generation", href: "/audio-generation", label: "音频生成", detail: "音频创作", icon: AudioLines },
  { id: "infinite-canvas", href: "/infinite-canvas", label: "无限画布", detail: "图片与视频生成", icon: PanelsTopLeft },
  { id: "prompt-templates", href: "/prompt-templates", label: "模板中心", detail: "提示词资产", icon: Sparkles },
  { id: "image-library", href: "/image-library", label: "历史", detail: "图片、视频与音频资产", icon: Library },
  { id: "monitoring", href: "/monitoring", label: "监控看板", detail: "运行状态", icon: Activity, adminOnly: true },
  { id: "costs", href: "/costs", label: "费用记录", detail: "模型与用户费用", icon: ReceiptText, adminOnly: true },
  { id: "users", href: "/users", label: "成员权限", detail: "团队管理", icon: Users, adminOnly: true },
  { id: "generation-records", href: "/image?history=1", activePath: "/image", activeQuery: { history: "1" }, label: "生成记录", detail: "图片、视频与音频任务", icon: History },
];

export type NavigationItem = { id: string; visible: boolean };
export const navigationLayout = ref<NavigationItem[]>(navItems.map(({ id }) => ({ id, visible: true })));

export function applyNavigationLayout(items: NavigationItem[]) {
  const byId = new Map(items.map((item) => [item.id, item]));
  navigationLayout.value = [
    ...items.filter((item) => navItems.some((definition) => definition.id === item.id)),
    ...navItems.filter((definition) => !byId.has(definition.id)).map(({ id }) => ({ id, visible: true })),
  ];
}

export async function fetchNavigationLayout() {
  const result = await httpRequest<{ items: NavigationItem[] }>("/api/navigation");
  applyNavigationLayout(result.items);
  return result.items;
}

export async function saveNavigationLayout(items: NavigationItem[]) {
  const result = await httpRequest<{ items: NavigationItem[] }>("/api/navigation", {
    method: "PUT", body: { items },
  });
  applyNavigationLayout(result.items);
  return result.items;
}
