"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Pencil, RefreshCw, Search, UserPlus, UserX } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  createUser,
  disableUser,
  fetchUsers,
  updateUser,
  type AuthRole,
  type UserAccount,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type UserFormState = {
  id?: string;
  username: string;
  password: string;
  name: string;
  role: AuthRole;
  enabled: boolean;
};

const emptyForm: UserFormState = {
  username: "",
  password: "",
  name: "",
  role: "user",
  enabled: true,
};

export default function UsersPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<UserAccount[]>([]);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [editing, setEditing] = useState<UserFormState | null>(null);

  const loadUsers = async () => {
    setIsLoading(true);
    try {
      const data = await fetchUsers();
      setItems(data.items);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取用户失败";
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!session) return;
    void loadUsers();
  }, [Boolean(session)]);

  const filteredItems = items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return true;
    return [item.username, item.name, item.role].some((value) => String(value || "").toLowerCase().includes(keyword));
  });

  const openEdit = (user: UserAccount) => {
    setEditing({
      id: user.id,
      username: user.username,
      password: "",
      name: user.name || "",
      role: user.role,
      enabled: user.enabled,
    });
  };

  const saveUser = async () => {
    if (!editing) return;
    if (!editing.id && (!editing.username.trim() || editing.password.length < 6)) {
      toast.error("新用户需要填写用户名和至少 6 位密码");
      return;
    }
    setIsSaving(true);
    try {
      if (editing.id) {
        await updateUser(editing.id, {
          name: editing.name,
          role: editing.role,
          enabled: editing.enabled,
          ...(editing.password ? { password: editing.password } : {}),
        });
        toast.success("用户已更新");
      } else {
        await createUser({
          username: editing.username.trim(),
          password: editing.password,
          name: editing.name.trim() || editing.username.trim(),
          role: editing.role,
          enabled: editing.enabled,
        });
        toast.success("用户已创建");
      }
      setEditing(null);
      await loadUsers();
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存用户失败";
      toast.error(message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDisable = async (user: UserAccount) => {
    if (user.id === session?.subjectId) {
      toast.error("不能停用当前登录账号");
      return;
    }
    try {
      await disableUser(user.id);
      toast.success("用户已停用");
      await loadUsers();
    } catch (error) {
      const message = error instanceof Error ? error.message : "停用用户失败";
      toast.error(message);
    }
  };

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <section className="flex h-[calc(100dvh-3.5rem)] min-h-0 flex-col overflow-hidden border-t border-slate-200 bg-[#f7f8fa] dark:border-white/10 dark:bg-[#111317]">
      <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-4 dark:border-white/10 dark:bg-[#16191f] sm:px-6">
        <div className="mx-auto flex max-w-[1280px] flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-semibold text-slate-950 dark:text-stone-50">用户管理</h1>
              <p className="mt-1 text-sm text-slate-500 dark:text-stone-400">
                管理公司内部账号、角色和启用状态。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="h-9 rounded-md border-slate-200 bg-white text-slate-700 shadow-none"
                onClick={() => void loadUsers()}
                disabled={isLoading}
              >
                {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                刷新
              </Button>
              <Button className="h-9 rounded-md bg-slate-950 text-white shadow-none hover:bg-slate-800" onClick={() => setEditing({ ...emptyForm })}>
                <UserPlus className="size-4" />
                新建用户
              </Button>
            </div>
          </div>
          <div className="relative max-w-[420px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索用户名、姓名、角色"
              className="h-10 rounded-md border-slate-200 bg-white pl-9 text-sm shadow-none dark:border-white/10 dark:bg-white/[0.04]"
            />
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        <div className="mx-auto max-w-[1280px] overflow-x-auto rounded-lg border border-slate-200 bg-white dark:border-white/10 dark:bg-[#16191f]">
          <div className="grid min-w-[860px] grid-cols-[1.2fr_1fr_120px_120px_180px] border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-medium text-slate-500 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-400">
            <span>账号</span>
            <span>姓名</span>
            <span>角色</span>
            <span>状态</span>
            <span className="text-right">操作</span>
          </div>
          {isLoading && items.length === 0 ? (
            <div className="p-6 text-sm text-slate-500">正在读取用户...</div>
          ) : filteredItems.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-500">暂无匹配用户。</div>
          ) : (
            filteredItems.map((user) => (
              <article
                key={user.id}
                className="grid min-w-[860px] grid-cols-[1.2fr_1fr_120px_120px_180px] items-center border-b border-slate-100 px-4 py-3 last:border-b-0 dark:border-white/10"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-slate-950 dark:text-stone-50">{user.username}</div>
                  <div className="mt-1 text-xs text-slate-500 dark:text-stone-400">
                    {user.last_login_at ? `最近登录 ${user.last_login_at}` : "尚未登录"}
                  </div>
                </div>
                <div className="truncate text-sm text-slate-700 dark:text-stone-200">{user.name}</div>
                <div>
                  <Badge variant={user.role === "admin" ? "info" : "outline"} className="rounded-md">
                    {user.role === "admin" ? "管理员" : "员工"}
                  </Badge>
                </div>
                <div>
                  <Badge variant={user.enabled ? "success" : "danger"} className="rounded-md">
                    {user.enabled ? "启用" : "停用"}
                  </Badge>
                </div>
                <div className="flex justify-end gap-1.5">
                  <Button variant="outline" size="sm" className="h-8 rounded-md border-slate-200 bg-white px-2 text-xs shadow-none" onClick={() => openEdit(user)}>
                    <Pencil className="size-3.5" />
                    编辑
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 rounded-md border-slate-200 bg-white px-2 text-xs shadow-none"
                    onClick={() => void handleDisable(user)}
                    disabled={!user.enabled || user.id === session.subjectId}
                  >
                    <UserX className="size-3.5" />
                  </Button>
                </div>
              </article>
            ))
          )}
        </div>
      </div>

      <Dialog open={Boolean(editing)} onOpenChange={(open) => (!open ? setEditing(null) : null)}>
        <DialogContent className="max-h-[88dvh] overflow-y-auto rounded-lg border-slate-200 bg-white p-6 shadow-lg dark:border-white/10 dark:bg-[#16191f]">
          <DialogHeader>
            <DialogTitle className="text-lg">{editing?.id ? "编辑用户" : "新建用户"}</DialogTitle>
          </DialogHeader>
          {editing ? (
            <div className="grid gap-3">
              <Input
                value={editing.username}
                onChange={(event) => setEditing({ ...editing, username: event.target.value })}
                placeholder="用户名"
                disabled={Boolean(editing.id)}
                className="rounded-md"
              />
              <Input
                value={editing.name}
                onChange={(event) => setEditing({ ...editing, name: event.target.value })}
                placeholder="姓名"
                className="rounded-md"
              />
              <Input
                type="password"
                value={editing.password}
                onChange={(event) => setEditing({ ...editing, password: event.target.value })}
                placeholder={editing.id ? "留空则不修改密码" : "至少 6 位密码"}
                className="rounded-md"
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <Select value={editing.role} onValueChange={(value) => setEditing({ ...editing, role: value as AuthRole })}>
                  <SelectTrigger className="h-10 rounded-md">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">员工</SelectItem>
                    <SelectItem value="admin">管理员</SelectItem>
                  </SelectContent>
                </Select>
                <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 text-sm text-slate-700 dark:border-white/10 dark:text-stone-200">
                  <Checkbox checked={editing.enabled} onCheckedChange={(checked) => setEditing({ ...editing, enabled: checked === true })} className="size-4 rounded-[4px]" />
                  启用账号
                </label>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" className="rounded-md" onClick={() => setEditing(null)}>取消</Button>
            <Button className="rounded-md bg-slate-950 text-white hover:bg-slate-800" onClick={() => void saveUser()} disabled={isSaving}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
