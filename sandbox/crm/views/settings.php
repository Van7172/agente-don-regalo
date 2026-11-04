<?php
$colorLabels = [
    'arena' => 'Arena',
    'terracota' => 'Terracota',
    'oliva' => 'Oliva',
    'azul' => 'Azul',
    'rosa' => 'Rosa',
    'gris' => 'Gris',
];
?>
<main class="settings" id="settings-app" data-base="<?= e(url_to('')) ?>"
      data-max-body="<?= (int) Repository::QUICK_REPLY_MAX ?>"
      data-colors="<?= e((string) json_encode($colorLabels, JSON_UNESCAPED_UNICODE)) ?>">
  <h2>Ajustes</h2>
  <p class="lead">Lo que usa el equipo a diario. Los cambios se aplican al recargar el inbox.</p>
  <div class="settings-error" id="settings-error" role="alert" hidden></div>

  <section class="card elev-sm settings-card">
    <h4>Respuestas rápidas</h4>
    <p class="settings-hint">Se insertan escribiendo <code>/</code> en el chat. El asesor siempre las revisa antes de enviar.</p>

    <form class="settings-form" id="reply-form">
      <input type="hidden" id="reply-id" />
      <div class="settings-form-row">
        <label>
          <span>Comando</span>
          <input class="input" id="reply-cmd" required maxlength="32" pattern="[a-z0-9\-]{2,32}"
                 placeholder="horarios" title="Minúsculas, números y guiones" />
        </label>
        <label class="grow">
          <span>Título</span>
          <input class="input" id="reply-label" required maxlength="120"
                 placeholder="Horarios de entrega" />
        </label>
      </div>
      <label>
        <span>Texto <small id="reply-count"></small></span>
        <textarea class="input" id="reply-body" rows="4" required
                  maxlength="<?= (int) Repository::QUICK_REPLY_MAX ?>"></textarea>
      </label>
      <div class="settings-actions">
        <button class="btn btn-primary" type="submit" id="reply-submit">Crear respuesta</button>
        <button class="btn btn-secondary" type="button" id="reply-cancel" hidden>Cancelar</button>
      </div>
    </form>

    <div class="settings-list" id="reply-list" aria-live="polite"></div>
  </section>

  <section class="card elev-sm settings-card">
    <h4>Etiquetas</h4>
    <p class="settings-hint">Una etiqueta archivada ya no se ofrece, pero los chats que la llevan la conservan.</p>

    <form class="settings-form" id="tag-form">
      <input type="hidden" id="tag-id" />
      <div class="settings-form-row">
        <label class="grow">
          <span>Nombre</span>
          <input class="input" id="tag-name" required maxlength="40" placeholder="Cumpleaños" />
        </label>
        <label>
          <span>Color</span>
          <select class="input" id="tag-color">
            <?php foreach ($colorLabels as $value => $label): ?>
              <option value="<?= e($value) ?>"><?= e($label) ?></option>
            <?php endforeach; ?>
          </select>
        </label>
      </div>
      <div class="settings-actions">
        <button class="btn btn-primary" type="submit" id="tag-submit">Crear etiqueta</button>
        <button class="btn btn-secondary" type="button" id="tag-cancel" hidden>Cancelar</button>
      </div>
    </form>

    <div class="settings-list" id="tag-list" aria-live="polite"></div>
  </section>
</main>
<script src="<?= e(url_to('assets/settings.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/settings.js') ?>"></script>
