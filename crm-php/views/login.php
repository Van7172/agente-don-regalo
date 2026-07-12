<div class="login-wrap">
  <form class="login-card" method="post" action="<?= e(url_to('login.php')) ?>">
    <h1>Don Regalo</h1>
    <p class="muted">CRM WhatsApp · acceso asesores</p>
    <?php if (!empty($error)): ?>
      <div class="alert"><?= e($error) ?></div>
    <?php endif; ?>
    <label>Login
      <input name="login" type="text" required autocomplete="username" value="<?= e($login ?? '') ?>" placeholder="Usuario" />
    </label>
    <label>Contraseña
      <input name="password" type="password" required autocomplete="current-password" placeholder="Contraseña" />
    </label>
    <button type="submit" class="primary">Entrar</button>
  </form>
</div>
