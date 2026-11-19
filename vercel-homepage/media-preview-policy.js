(function attachEverythingHubMediaPreviewPolicy(root) {
  function isMediaPreviewAllowed(item) {
    if (!item || typeof item !== "object") return false;
    const source = String(item.source || "").trim();
    const license = String(item.license || "").trim().toLowerCase();
    const licenseUrl = String(item.licenseUrl || "").trim();
    const attributionRequired = item.attributionRequired;
    const creator = String(item.creator || item.credit || "").trim();

    const publicDomain = license.startsWith("public domain") || license.startsWith("cc0");
    const ccBy = license.startsWith("cc by ") || license.startsWith("cc by-sa ");
    const licenseUrlValid = licenseUrl.startsWith("https://") || licenseUrl.startsWith("http://");
    const licenseValid = publicDomain || (ccBy && licenseUrlValid);
    const attributionValid = attributionRequired === false ||
      (attributionRequired === true && creator.length > 0);

    // Bing results are discovery links only. Preview embeds require explicit
    // Commons license metadata and a satisfied attribution requirement.
    return source === "Wikimedia Commons" && licenseValid && attributionValid;
  }

  const api = Object.freeze({ isMediaPreviewAllowed });
  if (typeof module === "object" && module && module.exports) module.exports = api;
  else root.EHMediaPreviewPolicy = api;
})(typeof globalThis === "object" ? globalThis : this);
