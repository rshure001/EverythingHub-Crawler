(function attachEverythingHubMediaPreviewPolicy(root) {
  function isMediaPreviewAllowed(item) {
    if (!item || typeof item !== "object") return false;
    const source = String(item.source || "").trim();
    const license = String(item.license || "").trim().toLowerCase();
    const licenseUrl = String(item.licenseUrl || "").trim();
    const attributionRequired = item.attributionRequired;
    const creator = String(item.creator || item.credit || "").trim();

    const publicDomain = license.startsWith("public domain");
    const cc0 = /^cc0(?:\s|$)/.test(license);
    const ccByMatch = license.match(/^cc by(-sa)?\s+(1\.0|2\.0|2\.5|3\.0|4\.0)$/);
    let ccByLicenseUrlValid = false;
    if (ccByMatch) {
      try {
        const url = new URL(licenseUrl);
        const kind = ccByMatch[1] ? "by-sa" : "by";
        const expectedPath = "/licenses/" + kind + "/" + ccByMatch[2] + "/";
        ccByLicenseUrlValid = url.protocol === "https:" &&
          url.hostname === "creativecommons.org" &&
          url.pathname.replace(/\/+$/, "/") === expectedPath;
      } catch {
        ccByLicenseUrlValid = false;
      }
    }

    const licenseValid = publicDomain || cc0 || (ccByMatch !== null && ccByLicenseUrlValid);
    // CC BY and CC BY-SA always require creator attribution, even if source
    // metadata incorrectly claims otherwise. Public-domain/CC0 records must
    // explicitly state whether attribution is required.
    const attributionValid = ccByMatch
      ? creator.length > 0
      : attributionRequired === false || (attributionRequired === true && creator.length > 0);

    // Bing results remain discovery links only. Embedded previews require
    // explicit Wikimedia Commons or Internet Archive licensing and satisfied attribution.
    return (source === "Wikimedia Commons" || source === "Internet Archive") && licenseValid && attributionValid;
  }

  const api = Object.freeze({ isMediaPreviewAllowed });
  if (typeof module === "object" && module && module.exports) module.exports = api;
  else root.EHMediaPreviewPolicy = api;
})(typeof globalThis === "object" ? globalThis : this);
