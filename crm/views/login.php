<?php
/** @var string $error */
/** @var string $login */
?>
<div class="login-split">
  <aside class="login-brand">
    <?= brand_mark('login') ?>
    <p class="brand-tagline">Regalos con alma,<br />atendidos con cuidado.</p>
    <p class="brand-sub">Panel interno para asesores que acompañan a Don Regalo, nuestro asistente de WhatsApp.</p>
  </aside>

  <main class="login-form-pane">
    <form class="login-form" method="post" action="<?= e(url_to('login.php')) ?>">
      <div>
        <h2>Bienvenido de vuelta</h2>
        <p class="lead">Ingresa con tu usuario de asesor Don Regalo.</p>
      </div>

      <?php if (!empty($error)): ?>
        <div class="alert" role="alert">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          <?= e($error) ?>
        </div>
      <?php endif; ?>

      <div class="field">
        <label for="login">Usuario</label>
        <input class="input" id="login" name="login" type="text" required
               autocomplete="username" value="<?= e($login ?? '') ?>" placeholder="Usuario" />
      </div>

      <div class="field">
        <label for="password">Contraseña</label>
        <div class="pw-wrap">
          <input class="input" id="password" name="password" type="password" required
                 autocomplete="current-password" placeholder="••••••••" />
          <button type="button" class="pw-toggle" id="pw-toggle" aria-label="Mostrar contraseña">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
              <circle cx="12" cy="12" r="3"></circle>
            </svg>
          </button>
        </div>
      </div>

      <button type="submit" class="btn btn-primary btn-block" style="padding:12px;font-size:14px;">Ingresar</button>
    </form>
  </main>
</div>

<script>
  (() => {
    const toggle = document.getElementById("pw-toggle");
    const input = document.getElementById("password");
    if (!toggle || !input) return;
    toggle.addEventListener("click", () => {
      const shown = input.type === "text";
      input.type = shown ? "password" : "text";
      toggle.setAttribute("aria-label", shown ? "Mostrar contraseña" : "Ocultar contraseña");
    });
  })();
</script>
