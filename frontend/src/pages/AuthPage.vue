<script setup lang="ts">
import { LoaderCircle, LockKeyhole, LogIn, RefreshCw, User, UserPlus } from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { toast } from "vue-sonner";

import { fetchCaptcha, login, register } from "@/lib/api";
import { getDefaultRouteForRole, setStoredAuthSession } from "@/stores/auth";
import { setSession } from "@/stores/session";

type AuthMode = "login" | "register";

const route = useRoute();
const router = useRouter();

const mode = computed<AuthMode>(() => (route.name === "register" ? "register" : "login"));
const isRegister = computed(() => mode.value === "register");
const title = computed(() => (isRegister.value ? "创建账号" : "欢迎回来"));
const subtitle = computed(() => (isRegister.value ? "新账号默认是普通用户，注册后直接进入工作台。" : "登录后继续处理图片生成、素材和历史任务。"));

const loginUsername = ref("admin");
const loginPassword = ref("");
const registerUsername = ref("");
const registerName = ref("");
const registerPassword = ref("");
const registerConfirmPassword = ref("");
const captchaId = ref("");
const captchaImage = ref("");
const captchaCode = ref("");
const isSubmitting = ref(false);
const isLoadingCaptcha = ref(false);

const switchLink = computed(() => ({
  name: isRegister.value ? "login" : "register",
  query: typeof route.query.next === "string" ? { next: route.query.next } : undefined,
}));

function targetAfterAuth(role: "admin" | "user") {
  const next = typeof route.query.next === "string" ? route.query.next : getDefaultRouteForRole(role);
  return next.startsWith("/") ? next : getDefaultRouteForRole(role);
}

async function persistSession(data: Awaited<ReturnType<typeof login>>) {
  const session = {
    key: data.token,
    role: data.role,
    subjectId: data.subject_id,
    username: data.username,
    name: data.name,
  };
  await setStoredAuthSession(session);
  setSession(session);
  await router.replace(targetAfterAuth(data.role));
}

async function refreshCaptcha() {
  isLoadingCaptcha.value = true;
  try {
    const data = await fetchCaptcha();
    captchaId.value = data.captcha_id;
    captchaImage.value = data.image_data_url;
    captchaCode.value = "";
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "验证码加载失败");
  } finally {
    isLoadingCaptcha.value = false;
  }
}

async function handleLogin() {
  if (!loginUsername.value.trim() || !loginPassword.value) {
    toast.error("请输入用户名和密码");
    return;
  }
  isSubmitting.value = true;
  try {
    const data = await login(loginUsername.value.trim(), loginPassword.value);
    await persistSession(data);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "登录失败");
  } finally {
    isSubmitting.value = false;
  }
}

async function handleRegister() {
  const username = registerUsername.value.trim();
  if (!username || !registerPassword.value) {
    toast.error("请输入用户名和密码");
    return;
  }
  if (registerPassword.value.length < 6) {
    toast.error("密码至少 6 位");
    return;
  }
  if (registerPassword.value !== registerConfirmPassword.value) {
    toast.error("两次输入的密码不一致");
    return;
  }
  if (!captchaId.value || !captchaCode.value.trim()) {
    toast.error("请输入图形验证码");
    return;
  }
  isSubmitting.value = true;
  try {
    const data = await register({
      username,
      password: registerPassword.value,
      name: registerName.value.trim(),
      captcha_id: captchaId.value,
      captcha_code: captchaCode.value.trim(),
    });
    await persistSession(data);
  } catch (error) {
    await refreshCaptcha();
    toast.error(error instanceof Error ? error.message : "注册失败");
  } finally {
    isSubmitting.value = false;
  }
}

watch(isRegister, (next) => {
  if (next && !captchaImage.value) void refreshCaptcha();
});

onMounted(() => {
  if (isRegister.value) void refreshCaptcha();
});
</script>

<template>
  <section class="auth-shell">
    <div class="auth-brand">
      <img src="/jiakemei-mark.svg" alt="" class="auth-brand__mark" />
      <div>
        <div class="auth-brand__name">AI Image Studio</div>
        <div class="auth-brand__meta">AI Creative Workspace</div>
      </div>
    </div>

    <div class="auth-stage" :class="{ 'is-register': isRegister }">
      <div class="auth-panel">
        <div class="auth-mode" role="tablist" aria-label="认证方式">
          <RouterLink :to="{ name: 'login', query: route.query }" class="auth-mode__item" :class="{ 'is-active': !isRegister }">登录</RouterLink>
          <RouterLink :to="{ name: 'register', query: route.query }" class="auth-mode__item" :class="{ 'is-active': isRegister }">注册</RouterLink>
        </div>

        <header class="auth-header">
          <div class="auth-badge"><component :is="isRegister ? UserPlus : LogIn" class="size-4" /></div>
          <div>
            <h1>{{ title }}</h1>
            <p>{{ subtitle }}</p>
          </div>
        </header>

        <div class="auth-forms">
          <form class="auth-form auth-form--login" :aria-hidden="isRegister" @submit.prevent="handleLogin">
            <label class="auth-field">
              <span>用户名</span>
              <div class="auth-input-wrap">
                <User class="size-4" />
                <input v-model="loginUsername" autocomplete="username" aria-label="用户名" />
              </div>
            </label>
            <label class="auth-field">
              <span>密码</span>
              <div class="auth-input-wrap">
                <LockKeyhole class="size-4" />
                <input v-model="loginPassword" type="password" autocomplete="current-password" aria-label="密码" placeholder="默认密码 admin123456" />
              </div>
            </label>
            <button type="submit" class="auth-submit" :disabled="isSubmitting || isRegister">
              <LoaderCircle v-if="isSubmitting && !isRegister" class="size-4 animate-spin" />
              <LogIn v-else class="size-4" />
              登录工作台
            </button>
          </form>

          <form class="auth-form auth-form--register" :aria-hidden="!isRegister" @submit.prevent="handleRegister">
            <label class="auth-field">
              <span>用户名</span>
              <div class="auth-input-wrap">
                <User class="size-4" />
                <input v-model="registerUsername" autocomplete="username" aria-label="用户名" />
              </div>
            </label>
            <label class="auth-field">
              <span>名称</span>
              <div class="auth-input-wrap">
                <UserPlus class="size-4" />
                <input v-model="registerName" autocomplete="name" aria-label="名称" />
              </div>
            </label>
            <div class="auth-field-grid">
              <label class="auth-field">
                <span>密码</span>
                <div class="auth-input-wrap">
                  <LockKeyhole class="size-4" />
                  <input v-model="registerPassword" type="password" autocomplete="new-password" aria-label="密码" />
                </div>
              </label>
              <label class="auth-field">
                <span>确认密码</span>
                <div class="auth-input-wrap">
                  <LockKeyhole class="size-4" />
                  <input v-model="registerConfirmPassword" type="password" autocomplete="new-password" aria-label="确认密码" />
                </div>
              </label>
            </div>
            <label class="auth-field">
              <span>图形验证码</span>
              <div class="auth-captcha">
                <div class="auth-input-wrap">
                  <input v-model="captchaCode" autocomplete="off" aria-label="图形验证码" placeholder="输入右侧字符" />
                </div>
                <button type="button" class="auth-captcha__image" :disabled="isLoadingCaptcha" @click="refreshCaptcha" aria-label="刷新验证码">
                  <LoaderCircle v-if="isLoadingCaptcha" class="size-4 animate-spin" />
                  <img v-else-if="captchaImage" :src="captchaImage" alt="图形验证码" />
                  <RefreshCw v-else class="size-4" />
                </button>
              </div>
            </label>
            <button type="submit" class="auth-submit" :disabled="isSubmitting || !isRegister">
              <LoaderCircle v-if="isSubmitting && isRegister" class="size-4 animate-spin" />
              <UserPlus v-else class="size-4" />
              注册并进入
            </button>
          </form>
        </div>

        <RouterLink :to="switchLink" class="auth-switch">
          {{ isRegister ? "已有账号，返回登录" : "没有账号，立即注册" }}
        </RouterLink>
      </div>

      <aside class="auth-visual" aria-hidden="true">
        <div class="auth-visual__card">
          <div class="auth-visual__label">Creative OS</div>
          <h2>{{ isRegister ? "新成员加入后即可共享图片生成能力。" : "把生成、素材和历史任务放在同一个工作台。" }}</h2>
          <div class="auth-visual__metrics">
            <span>10 API keys</span>
            <span>Redis slots</span>
            <span>Team ready</span>
          </div>
        </div>
        <div class="auth-orbit auth-orbit--one"></div>
        <div class="auth-orbit auth-orbit--two"></div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.auth-shell {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 32px;
  background:
    radial-gradient(circle at 18% 18%, rgba(14, 116, 244, 0.08), transparent 34%),
    linear-gradient(135deg, #f8fafc 0%, #eef2ff 100%);
  color: #07111f;
}

.auth-brand {
  position: fixed;
  top: 24px;
  left: 32px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.auth-brand__mark {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: #020617;
}

.auth-brand__name {
  font-size: 16px;
  font-weight: 700;
}

.auth-brand__meta {
  margin-top: 2px;
  font-size: 12px;
  font-weight: 600;
  color: #516179;
}

.auth-stage {
  width: min(980px, 100%);
  min-height: 640px;
  display: grid;
  grid-template-columns: minmax(360px, 440px) 1fr;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 20px;
  background: #ffffff;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.10);
}

.auth-panel {
  position: relative;
  z-index: 1;
  padding: 34px;
  display: flex;
  flex-direction: column;
}

.auth-mode {
  width: 184px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 4px;
  border-radius: 12px;
  background: #f1f5f9;
}

.auth-mode__item {
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  font-size: 13px;
  font-weight: 700;
  color: #56657c;
  transition: background 180ms ease, color 180ms ease, transform 180ms ease;
}

.auth-mode__item.is-active {
  background: #ffffff;
  color: #07111f;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.10);
}

.auth-header {
  margin-top: 34px;
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.auth-badge {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 12px;
  background: #07111f;
  color: #ffffff;
}

.auth-header h1 {
  margin: 0;
  font-size: 24px;
  line-height: 1.2;
  font-weight: 800;
  text-wrap: balance;
}

.auth-header p {
  max-width: 38ch;
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: #516179;
}

.auth-forms {
  position: relative;
  margin-top: 28px;
  min-height: 238px;
  overflow: visible;
  transition: min-height 200ms ease;
}

.auth-stage.is-register .auth-forms {
  min-height: 430px;
}

.auth-form {
  position: absolute;
  inset: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  transition: transform 240ms cubic-bezier(0.22, 1, 0.36, 1), opacity 180ms ease;
}

.auth-form--login {
  transform: translateX(0);
  opacity: 1;
}

.auth-form--register {
  transform: translateX(34px);
  opacity: 0;
  pointer-events: none;
}

.auth-stage.is-register .auth-form--login {
  transform: translateX(-34px);
  opacity: 0;
  pointer-events: none;
}

.auth-stage.is-register .auth-form--register {
  transform: translateX(0);
  opacity: 1;
  pointer-events: auto;
}

.auth-field,
.auth-field-grid {
  display: grid;
  gap: 8px;
}

.auth-field-grid {
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.auth-field span {
  font-size: 13px;
  font-weight: 700;
  color: #1e2b3e;
}

.auth-input-wrap {
  height: 44px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #ffffff;
  padding: 0 13px;
  color: #516179;
  transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.auth-input-wrap:focus-within {
  border-color: #1476e8;
  box-shadow: 0 0 0 3px rgba(20, 118, 232, 0.14);
}

.auth-input-wrap input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: #07111f;
  font-size: 14px;
}

.auth-input-wrap input::placeholder {
  color: #617089;
}

.auth-captcha {
  display: grid;
  grid-template-columns: 1fr 150px;
  gap: 10px;
}

.auth-captcha__image {
  height: 44px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid #dbe3ee;
  border-radius: 12px;
  background: #f8fafc;
  color: #516179;
}

.auth-captcha__image img {
  width: 150px;
  height: 44px;
  object-fit: cover;
}

.auth-submit {
  height: 46px;
  margin-top: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  border-radius: 12px;
  background: #0875e1;
  color: #ffffff;
  font-size: 14px;
  font-weight: 800;
  transition: background 160ms ease, transform 160ms ease;
}

.auth-submit:hover:not(:disabled) {
  background: #0566c8;
  transform: translateY(-1px);
}

.auth-submit:disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.auth-switch {
  position: relative;
  z-index: 2;
  align-self: flex-end;
  margin-top: 10px;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.5;
  color: #0875e1;
}

.auth-visual {
  position: relative;
  display: grid;
  place-items: center;
  overflow: hidden;
  background:
    radial-gradient(circle at 76% 20%, rgba(255, 255, 255, 0.18), transparent 28%),
    linear-gradient(135deg, #07111f 0%, #1557d0 52%, #7c3aed 100%);
}

.auth-visual__card {
  position: relative;
  z-index: 1;
  width: min(360px, 72%);
  min-height: 360px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding: 28px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
  backdrop-filter: blur(10px);
}

.auth-visual__label {
  position: absolute;
  top: 24px;
  left: 24px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.16);
  padding: 0 12px;
  font-size: 12px;
  font-weight: 800;
}

.auth-visual__card h2 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
  font-weight: 850;
  text-wrap: balance;
}

.auth-visual__metrics {
  margin-top: 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.auth-visual__metrics span {
  height: 28px;
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.18);
  padding: 0 10px;
  font-size: 12px;
  font-weight: 800;
}

.auth-orbit {
  position: absolute;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
}

.auth-orbit--one {
  width: 420px;
  height: 420px;
  right: -160px;
  top: -120px;
}

.auth-orbit--two {
  width: 300px;
  height: 300px;
  left: -120px;
  bottom: -80px;
}

@media (max-width: 860px) {
  .auth-shell {
    padding: 96px 16px 24px;
  }

  .auth-brand {
    left: 18px;
    top: 18px;
  }

  .auth-stage {
    min-height: 0;
    grid-template-columns: 1fr;
  }

  .auth-panel {
    min-height: 0;
    padding: 24px;
  }

  .auth-visual {
    min-height: 240px;
  }

  .auth-visual__card {
    width: calc(100% - 48px);
    min-height: 190px;
  }
}

@media (max-width: 520px) {
  .auth-stage.is-register .auth-forms {
    min-height: 548px;
  }

  .auth-field-grid,
  .auth-captcha {
    grid-template-columns: 1fr;
  }

  .auth-captcha__image,
  .auth-captcha__image img {
    width: 100%;
  }

  .auth-panel {
    min-height: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .auth-form,
  .auth-mode__item,
  .auth-input-wrap,
  .auth-submit {
    transition: none;
  }
}
</style>
