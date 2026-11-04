<?php
/** @var array<string, array<string, string>> $tonos */
/** @var array<string, array<string, string>> $formatos */
/** @var string $base */
?>
<div class="studio" id="studio-app" data-base="<?= e($base) ?>">
  <header class="studio-head">
    <h2>Crear contenido</h2>
    <p class="lead">
      Un post de redes a partir de un producto real del catálogo. El precio y la
      foto salen del catálogo, no de la IA.
    </p>
  </header>

  <div class="alert error-box" id="studio-error" role="alert" hidden></div>

  <!-- 1 ─ Elige producto ------------------------------------------------- -->
  <section class="studio-step is-open" id="step-product" aria-labelledby="step-product-title">
    <h3 class="studio-step-title" id="step-product-title">
      <span class="studio-step-num">1</span> Elige producto
    </h3>
    <div class="studio-step-body">
      <div class="field">
        <label for="product-search">Busca en el catálogo</label>
        <input type="search" id="product-search" autocomplete="off" spellcheck="false"
               placeholder="Escribe al menos 3 letras: desayuno, orquídea, peluche…" />
        <p class="field-hint" id="search-hint">El buscador se activa a partir de 3 caracteres.</p>
      </div>
      <div class="product-results" id="product-results" hidden></div>
      <div class="product-chosen" id="product-chosen" hidden></div>
    </div>
  </section>

  <!-- 2 ─ Tipo de pieza -------------------------------------------------- -->
  <section class="studio-step" id="step-format" aria-labelledby="step-format-title">
    <h3 class="studio-step-title" id="step-format-title">
      <span class="studio-step-num">2</span> Tipo de pieza
    </h3>
    <div class="studio-step-body">
      <div class="format-grid" role="radiogroup" aria-label="Formato de la pieza">
        <?php $primero = true; foreach ($formatos as $slug => $f): ?>
        <label class="format-option<?= $primero ? ' is-active' : '' ?>">
          <input type="radio" name="formato" value="<?= e($slug) ?>"<?= $primero ? ' checked' : '' ?> />
          <span class="format-frame is-<?= e(str_replace(':', '-', $slug)) ?>"></span>
          <span class="format-label"><?= e($f['label']) ?></span>
          <span class="format-hint"><?= e($f['hint']) ?></span>
        </label>
        <?php $primero = false; endforeach; ?>
      </div>
    </div>
  </section>

  <!-- 3 ─ Instrucciones -------------------------------------------------- -->
  <section class="studio-step" id="step-brief" aria-labelledby="step-brief-title">
    <h3 class="studio-step-title" id="step-brief-title">
      <span class="studio-step-num">3</span> Instrucciones
    </h3>
    <div class="studio-step-body">
      <div class="brief-grid">
        <div class="field">
          <label for="tone">Tono</label>
          <select id="tone">
            <?php foreach ($tonos as $slug => $t): ?>
            <option value="<?= e($slug) ?>"><?= e($t['label']) ?> — <?= e($t['hint']) ?></option>
            <?php endforeach; ?>
          </select>
        </div>
        <div class="field">
          <label for="variants">Variantes</label>
          <select id="variants">
            <option value="1">1 versión</option>
            <option value="2">2 versiones</option>
            <option value="3" selected>3 versiones para elegir</option>
          </select>
        </div>
      </div>

      <fieldset class="toggle-set">
        <legend>Incluir en el post</legend>
        <label class="toggle">
          <input type="checkbox" id="opt-price" checked />
          <span>Precio del producto <em>(sale del catálogo, en soles)</em></span>
        </label>
        <label class="toggle">
          <input type="checkbox" id="opt-cta" checked />
          <span>Llamada a la acción (CTA)</span>
        </label>
      </fieldset>

      <div class="field">
        <label for="brief">Indicaciones (opcional)</label>
        <textarea id="brief" rows="3"
                  placeholder="Ej.: enfócalo en Día de la Madre, menciona entrega el mismo día en Lima…"></textarea>
      </div>

      <button type="button" class="btn btn-primary" id="btn-generate" disabled>
        Generar contenido
      </button>
      <p class="field-hint" id="generate-hint">Primero elige un producto.</p>
    </div>
  </section>

  <!-- 4 ─ Resultado ------------------------------------------------------ -->
  <section class="studio-step" id="step-result" aria-labelledby="step-result-title">
    <h3 class="studio-step-title" id="step-result-title">
      <span class="studio-step-num">4</span> Resultado
    </h3>
    <div class="studio-step-body">
      <div class="result-empty empty-state" id="result-empty">
        <h3>Todavía no hay nada</h3>
        <p>Elige un producto, ajusta las instrucciones y pulsa «Generar contenido».</p>
      </div>
      <div class="result-grid" id="result-grid" hidden>
        <div class="result-preview">
          <div class="preview-frame is-9-16" id="preview-frame">
            <img id="preview-img" alt="Foto del producto" />
            <div class="preview-overlay">
              <div class="preview-title" id="preview-title"></div>
              <div class="preview-body" id="preview-body"></div>
              <div class="preview-price" id="preview-price"></div>
              <div class="preview-cta" id="preview-cta"></div>
            </div>
          </div>
          <p class="field-hint">
            Vista de referencia: así queda el texto sobre la foto. La pieza final
            se arma en Canva o Instagram con el texto y la foto de al lado.
          </p>
          <a class="btn btn-secondary btn-block" id="btn-photo" download target="_blank" rel="noopener">
            Descargar foto del producto
          </a>
        </div>
        <div class="result-copies" id="result-copies"></div>
      </div>
    </div>
  </section>
</div>

<script src="<?= e(url_to('assets/content.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/content.js') ?>"></script>
