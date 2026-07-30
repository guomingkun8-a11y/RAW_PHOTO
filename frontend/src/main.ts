import { createApp } from "vue";
import { Toaster } from "vue-sonner";

import App from "@/App.vue";
import { router } from "@/router";
import "@/styles.css";

const storedTheme = localStorage.getItem("chatgpt2api-theme");
const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.classList.toggle("dark", storedTheme ? storedTheme === "dark" : prefersDark);

const app = createApp(App);
app.use(router);
app.component("AppToaster", Toaster);
app.mount("#app");
