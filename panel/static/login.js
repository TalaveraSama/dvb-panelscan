"use strict";
const form = document.getElementById("loginForm");
document.getElementById("showPassword").addEventListener("change", (event) => {
  document.getElementById("password").type = event.target.checked
    ? "text"
    : "password";
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("loginSubmit");
  const error = document.getElementById("loginError");
  error.textContent = "";
  button.disabled = true;
  button.textContent = "Verificando acceso…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.elements.username.value.trim(),
        password: form.elements.password.value,
      }),
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(result.error || "No se pudo iniciar sesión");
    form.elements.password.value = "";
    window.location.replace("/");
  } catch (reason) {
    error.textContent = reason.message;
  } finally {
    button.disabled = false;
    button.textContent = "Entrar al panel →";
  }
});
