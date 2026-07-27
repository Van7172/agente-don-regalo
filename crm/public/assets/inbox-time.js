(function (global) {
  "use strict";

  function parseTimestamp(value) {
    if (!value) return null;
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : new Date(value.getTime());
    }
    const raw = String(value);
    const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function clockLabel(value) {
    const date = parseTimestamp(value);
    if (!date) return "";
    return date.toLocaleTimeString("es-PE", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  function localDayStart(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  /**
   * Etiqueta de la lista, como WhatsApp pero sin fechas ambiguas:
   * hoy → hora, ayer → "Ayer", antes → dd/mm/aaaa.
   */
  function conversationTimeLabel(value, now) {
    const date = parseTimestamp(value);
    if (!date) return "";
    const reference = parseTimestamp(now) || new Date();
    const days = Math.round(
      (localDayStart(reference) - localDayStart(date)) / 86400000
    );

    if (days === 0) return clockLabel(date);
    if (days === 1) return "Ayer";
    return date.toLocaleDateString("es-PE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  }

  function localDayKey(value) {
    const date = parseTimestamp(value);
    if (!date) return "";
    return [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, "0"),
      String(date.getDate()).padStart(2, "0"),
    ].join("-");
  }

  global.CrmInboxTime = Object.freeze({
    parseTimestamp,
    clockLabel,
    conversationTimeLabel,
    localDayKey,
  });
})(typeof window !== "undefined" ? window : globalThis);
