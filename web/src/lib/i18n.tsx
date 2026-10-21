"use client";

/**
 * Bilingual UI — English and Kannada.
 *
 * Karnataka Police operate in Kannada. A platform for the SCRB that is English-only
 * is not "mostly translated", it is unusable for a share of the people expected to
 * work in it for hours a day, and government digital services here are expected to
 * be bilingual.
 *
 * Before this, "bilingual" meant one thing: the voice console passed a `lang` hint
 * to the LLM so the *answer* came back in Kannada. Every page, heading, table column,
 * filter and empty state was English, `<html lang>` was hardcoded to "en", and the
 * webfonts loaded `subsets: ["latin"]` only — so even Kannada text from the model
 * fell back to whatever the OS had.
 *
 * A NOTE ON THE TRANSLATIONS
 * --------------------------
 * The Kannada below covers navigation and common UI chrome, where the vocabulary is
 * ordinary. It has NOT been reviewed by a native speaker, and police terminology has
 * conventions that general translation gets wrong — the difference between a
 * "chargesheet" and a "final report", or which word a Kannada-speaking officer
 * actually uses for "pendency". Shipping machine-translated legal vocabulary into a
 * police tool and calling it localised would be the same overclaim this codebase has
 * spent its effort removing.
 *
 * So: a missing key falls back to English rather than to a placeholder, `missingKeys()`
 * reports what is uncovered, and the dictionary is deliberately small and honest
 * rather than large and unverified. Domain vocabulary needs a Kannada-speaking officer
 * before it goes near a real deployment.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

export type Lang = "en" | "kn";

export const LANGUAGES: { code: Lang; label: string; native: string }[] = [
  { code: "en", label: "EN", native: "English" },
  { code: "kn", label: "ಕ", native: "ಕನ್ನಡ" },
];

const STORAGE_KEY = "ksp.lang";

/**
 * Only keys whose Kannada is ordinary vocabulary are listed. Anything absent falls
 * back to English on purpose — see the note above.
 */
const KN: Record<string, string> = {
  // navigation
  "nav.overview": "ಅವಲೋಕನ",
  "nav.cases": "ಪ್ರಕರಣಗಳು",
  "nav.investigate": "ತನಿಖೆ",
  "nav.map": "ನಕ್ಷೆ",
  "nav.network": "ಜಾಲ",
  "nav.analytics": "ವಿಶ್ಲೇಷಣೆ",
  "nav.models": "ಮಾದರಿಗಳು",
  "nav.registry": "ನೋಂದಣಿ",
  "nav.officers": "ಅಧಿಕಾರಿಗಳು",
  "nav.lifecycle": "ಜೀವನಚಕ್ರ",
  "nav.pendency": "ಬಾಕಿ",
  "nav.dispatch": "ನಿಯೋಜನೆ",
  "nav.access": "ಖಾತೆ ಮತ್ತು ಪಾತ್ರಗಳು",
  "nav.cctv": "ಸಿಸಿಟಿವಿ",
  "nav.news": "ಸುದ್ದಿ",
  "nav.copilot": "ಸಹಾಯಕ",
  "nav.more": "ಇನ್ನಷ್ಟು",

  // common chrome
  "common.district": "ಜಿಲ್ಲೆ",
  "common.districts": "ಜಿಲ್ಲೆಗಳು",
  "common.status": "ಸ್ಥಿತಿ",
  "common.search": "ಹುಡುಕಿ",
  "common.filter": "ಶೋಧಕ",
  "common.all": "ಎಲ್ಲಾ",
  "common.open": "ತೆರೆದ",
  "common.closed": "ಮುಚ್ಚಿದ",
  "common.loading": "ಲೋಡ್ ಆಗುತ್ತಿದೆ…",
  "common.export": "ರಫ್ತು",
  "common.signIn": "ಸೈನ್ ಇನ್",
  "common.signOut": "ಸೈನ್ ಔಟ್",
  "common.date": "ದಿನಾಂಕ",
  "common.name": "ಹೆಸರು",
  "common.total": "ಒಟ್ಟು",
  "common.unavailable": "ಲಭ್ಯವಿಲ್ಲ",
  "common.skipToContent": "ಮುಖ್ಯ ವಿಷಯಕ್ಕೆ ಹೋಗಿ",

  // a11y / accessible names
  "a11y.primaryNav": "ಮುಖ್ಯ ಸಂಚಾರ",
  "a11y.sendQuery": "ಪ್ರಶ್ನೆ ಕಳುಹಿಸಿ",
  "a11y.voiceOn": "ಧ್ವನಿ ಆನ್ ಮಾಡಿ",
  "a11y.voiceOff": "ಧ್ವನಿ ಆಫ್ ಮಾಡಿ",

  // ------------------------------------------------------------------------
  // Sign-in landing, profile menu and the Account & roles page.
  //
  // These go further than the "ordinary vocabulary" rule above: a sign-in page
  // that switches to Kannada for its buttons and stays English for its errors is
  // worse than either. Several strings carry police terms (FIR, watchlist,
  // recidivism, jurisdiction) and are exactly the kind the note at the top says
  // must be reviewed by a Kannada-speaking officer before a real deployment.
  // ------------------------------------------------------------------------

  // landing — hero
  "landing.ksp": "ಕರ್ನಾಟಕ ರಾಜ್ಯ ಪೊಲೀಸ್",
  "landing.scrb": "ರಾಜ್ಯ ಅಪರಾಧ ದಾಖಲೆಗಳ ಬ್ಯೂರೋ",
  "landing.emblemAlt": "ಕರ್ನಾಟಕ ಸರ್ಕಾರದ ಲಾಂಛನ",
  "landing.eyebrow": "ಅಪರಾಧ ಗುಪ್ತಚರ ವೇದಿಕೆ",
  "landing.tagline":
    "ರಾಜ್ಯ ಅಪರಾಧ ದಾಖಲೆಗಳ ಬ್ಯೂರೋಗಾಗಿ ಸಂಯೋಜಿತ ಅಪರಾಧ ಗುಪ್ತಚರ — ಪ್ರಕರಣಗಳು, ಅಪರಾಧಿಗಳು, ಜಾಲಗಳು ಮತ್ತು ನೇರ ಸಿಸಿಟಿವಿ, ಎಲ್ಲವೂ ಲೆಕ್ಕಪರಿಶೋಧಿತ ಒಂದೇ ಕಾರ್ಯಕ್ಷೇತ್ರದಲ್ಲಿ.",
  "landing.stat.firs": "ದಾಖಲಾದ ಎಫ್‌ಐಆರ್‌ಗಳು",
  "landing.stat.offenders": "ನಿಗಾದಲ್ಲಿರುವ ಅಪರಾಧಿಗಳು",
  "landing.stat.districts": "ಪೊಲೀಸ್ ಜಿಲ್ಲೆಗಳು",
  "landing.stat.links": "ಜಾಲ ಸಂಬಂಧಗಳು",
  "landing.statsDown": "ನೇರ ಅಂಕಿಅಂಶಗಳು ಲಭ್ಯವಿಲ್ಲ — ಗುಪ್ತಚರ ಕೇಂದ್ರ ಪ್ರತಿಕ್ರಿಯಿಸಲಿಲ್ಲ.",
  "landing.mapTitle": "ಕರ್ನಾಟಕದ ಪೊಲೀಸ್ ಜಿಲ್ಲೆಗಳು",
  "landing.legendArcs": "SCRB ಇಂದ ಜಿಲ್ಲಾ ಕೇಂದ್ರಗಳಿಗೆ — ಅಲಂಕಾರಿಕ ಮಾತ್ರ",
  "landing.legendAlerts": "ಹೊಸ ಪ್ರವೃತ್ತಿ ಎಚ್ಚರಿಕೆ, ಕಳೆದ 30 ದಿನಗಳು",
  "landing.simulated":
    "ಅಂಕಿಅಂಶಗಳನ್ನು ಕರ್ನಾಟಕದ ನೈಜ ಜಿಲ್ಲೆಗಳ ಮೇಲೆ ರಚಿಸಿದ, CCTNS ಮಾದರಿಯ ಅನುಕರಿತ ದತ್ತಾಂಶದಿಂದ ನೇರವಾಗಿ ಲೆಕ್ಕಹಾಕಲಾಗುತ್ತದೆ. ಯಾವುದೇ ನೈಜ KSP ದಾಖಲೆಗಳನ್ನು ಬಳಸಲಾಗಿಲ್ಲ.",

  // landing — sign-in card
  "landing.secure": "ಸುರಕ್ಷಿತ ಪ್ರವೇಶ",
  "landing.useOfficial": "ನಿಮ್ಮ ಅಧಿಕೃತ @ksp.gov.in ವಿಳಾಸವನ್ನು ಬಳಸಿ",
  "landing.returnTo": "ಸೈನ್ ಇನ್ ನಂತರ ನೀವು ಹಿಂತಿರುಗುವ ಪುಟ:",
  "landing.opening": "ಸೈನ್ ಇನ್ ಆಗಿದೆ — ನಿಮ್ಮ ಕಾರ್ಯಕ್ಷೇತ್ರ ತೆರೆಯಲಾಗುತ್ತಿದೆ…",
  "landing.checking": "ನಿಮ್ಮ ಸೆಷನ್ ಮರುಸ್ಥಾಪಿಸಲಾಗುತ್ತಿದೆ…",
  "landing.email": "ಅಧಿಕೃತ ಇಮೇಲ್",
  "landing.password": "ಪಾಸ್‌ವರ್ಡ್",
  "landing.show": "ಪಾಸ್‌ವರ್ಡ್ ತೋರಿಸಿ",
  "landing.hide": "ಪಾಸ್‌ವರ್ಡ್ ಮರೆಮಾಡಿ",
  "landing.capsLock": "ಕ್ಯಾಪ್ಸ್ ಲಾಕ್ ಆನ್ ಆಗಿದೆ",
  "landing.coreOnline": "ವ್ಯವಸ್ಥೆ ಚಾಲನೆಯಲ್ಲಿದೆ",
  "landing.coreOffline": "ವ್ಯವಸ್ಥೆ ಲಭ್ಯವಿಲ್ಲ — ಸೈನ್ ಇನ್ ವಿಫಲವಾಗುತ್ತದೆ",
  "landing.coreChecking": "ಸಂಪರ್ಕಿಸಲಾಗುತ್ತಿದೆ…",
  "landing.signingIn": "ಸೈನ್ ಇನ್ ಆಗುತ್ತಿದೆ…",
  "landing.err.invalid": "ಈ ವಿವರಗಳು ಗುರುತಿಸಲ್ಪಡಲಿಲ್ಲ. ವಿಳಾಸ ಮತ್ತು ಪಾಸ್‌ವರ್ಡ್ ಪರಿಶೀಲಿಸಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
  "landing.err.locked": "ಹಲವು ವಿಫಲ ಪ್ರಯತ್ನಗಳು — ಈ ಖಾತೆಯ ಸೈನ್ ಇನ್ ತಾತ್ಕಾಲಿಕವಾಗಿ ನಿಲ್ಲಿಸಲಾಗಿದೆ.",
  "landing.retryIn": "ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಲು ಉಳಿದ ಸಮಯ:",
  "landing.err.network": "ಗುಪ್ತಚರ ಕೇಂದ್ರವನ್ನು ತಲುಪಲಾಗಲಿಲ್ಲ. ಬ್ಯಾಕೆಂಡ್ ಚಾಲನೆಯಲ್ಲಿದೆಯೇ ಎಂದು ಪರಿಶೀಲಿಸಿ, ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
  "landing.err.demoOff": "ಈ ನಿಯೋಜನೆಯಲ್ಲಿ ಡೆಮೊ ಪ್ರವೇಶ ಆಫ್ ಆಗಿದೆ.",
  "landing.err.demo": "ಈ ಡೆಮೊ ಪಾತ್ರವನ್ನು ತೆರೆಯಲಾಗಲಿಲ್ಲ",
  "landing.err.server": "ಸರ್ವರ್‌ನಲ್ಲಿ ಸೈನ್ ಇನ್ ವಿಫಲವಾಯಿತು",
  "landing.restoreFailed": "ಗುಪ್ತಚರ ಕೇಂದ್ರ ಪ್ರತಿಕ್ರಿಯಿಸದ ಕಾರಣ ನಿಮ್ಮ ಉಳಿಸಿದ ಸೆಷನ್ ಪರಿಶೀಲಿಸಲಾಗಲಿಲ್ಲ.",
  "landing.explore": "ಅಥವಾ ಖಾತೆ ಇಲ್ಲದೆ ಅನ್ವೇಷಿಸಿ",
  "landing.tile.state": "ರಾಜ್ಯ",
  "landing.tile.district": "ಜಿಲ್ಲೆ",
  "landing.tile.station": "ಠಾಣೆ",
  "landing.pickDistrict": "ಜಿಲ್ಲೆ ಆಯ್ಕೆ —",
  "landing.enterAs": "ಪ್ರವೇಶಿಸಿ —",
  "landing.demoNote": "ಡೆಮೊ ಮೋಡ್ ಮಾತ್ರ: ಪ್ರತಿಯೊಂದೂ ಆ ವ್ಯಾಪ್ತಿಯ ಸಿದ್ಧ ಪಾತ್ರವನ್ನು ತೆರೆಯುತ್ತದೆ. ಪ್ರತಿ ಪ್ರವೇಶವನ್ನು ಲೆಕ್ಕಪರಿಶೋಧನೆಗೆ ದಾಖಲಿಸಲಾಗುತ್ತದೆ.",
  "landing.analyst": "ವಿಶ್ಲೇಷಕರಾಗಿ ಮುಂದುವರಿಸಿ (ಒಟ್ಟು ಅಂಕಿಅಂಶಗಳು ಮಾತ್ರ)",
  "landing.audited": "ಯಶಸ್ವಿಯಾಗಲಿ ಇಲ್ಲದಿರಲಿ, ಪ್ರತಿ ಸೈನ್ ಇನ್ ಪ್ರಯತ್ನವನ್ನು ಲೆಕ್ಕಪರಿಶೋಧನಾ ದಾಖಲೆಯಲ್ಲಿ ಬರೆಯಲಾಗುತ್ತದೆ.",

  // profile menu
  "profile.menu": "ಖಾತೆ ಮೆನು",
  "profile.demoAccess": "ಡೆಮೊ ಪ್ರವೇಶ",
  "profile.signedIn": "ಸೈನ್ ಇನ್ ಆಗಿದೆ",
  "profile.anonymous": "ಸೈನ್ ಇನ್ ಆಗಿಲ್ಲ",
  "profile.allDistricts": "ಎಲ್ಲಾ 31 ಜಿಲ್ಲೆಗಳು",
  "profile.station": "ಠಾಣೆ",
  "profile.districtSuffix": "ಜಿಲ್ಲೆ",
  "profile.switchRole": "ಪಾತ್ರ ಬದಲಿಸಿ (ಡೆಮೊ)",
  "profile.current": "ಪ್ರಸ್ತುತ",
  "profile.chooseDistrict": "ಜಿಲ್ಲೆ",
  "profile.switch": "ಬದಲಿಸಿ",
  "profile.switching": "ಬದಲಾಯಿಸಲಾಗುತ್ತಿದೆ…",
  "profile.switchFailed": "ಪಾತ್ರ ಬದಲಿಸಲಾಗಲಿಲ್ಲ",

  // account & roles
  "account.title": "ಖಾತೆ ಮತ್ತು ಪಾತ್ರಗಳು",
  "account.intro":
    "ಪ್ರತಿ ಪಾತ್ರಕ್ಕೂ ಒಟ್ಟು ಗುಪ್ತಚರ ಲಭ್ಯ. ಪ್ರಕರಣ ಕಡತಗಳು, ವಿವರ ಕಡತಗಳು ಮತ್ತು ಹೆಸರುಗಳಿರುವ ನಿಗಾ ಪಟ್ಟಿಗಳು ವ್ಯಕ್ತಿಮಟ್ಟದ ದಾಖಲೆಗಳು: ಪ್ರತಿ ಪಾತ್ರವು ತನ್ನ ಹುದ್ದೆ ಮತ್ತು ವ್ಯಾಪ್ತಿಗೆ ಬೇಕಾದಷ್ಟನ್ನು ಮಾತ್ರ ನೋಡುತ್ತದೆ, ಇದನ್ನು API ಜಾರಿಗೊಳಿಸುತ್ತದೆ, ಮತ್ತು ಪ್ರತಿ ಪ್ರವೇಶವನ್ನು ಲೆಕ್ಕಪರಿಶೋಧಿಸಲಾಗುತ್ತದೆ.",
  "account.signedInAs": "ಸೈನ್ ಇನ್ ಆದವರು",
  "account.visitor": "ಸಂದರ್ಶಕ",
  "account.analyst": "ವಿಶ್ಲೇಷಕ",
  "account.tier": "ಹಂತ",
  "account.jurisdiction": "ವ್ಯಾಪ್ತಿ",
  "account.anonNote": "ಸೈನ್ ಇನ್ ಇಲ್ಲದಿದ್ದರೆ API ನಿಮ್ಮನ್ನು ವಿಶ್ಲೇಷಕರೆಂದು ಪರಿಗಣಿಸುತ್ತದೆ: ಒಟ್ಟು ಅಂಕಿಅಂಶಗಳು ಮಾತ್ರ, ವ್ಯಕ್ತಿಮಟ್ಟದ ಭಾಗಗಳು ಗುಪ್ತನಾಮದಲ್ಲಿ.",
  "account.meDown": "ಗುಪ್ತಚರ ಕೇಂದ್ರ ಪ್ರತಿಕ್ರಿಯಿಸಲಿಲ್ಲ, ಆದ್ದರಿಂದ ನಿಮ್ಮ ಗುರುತನ್ನು ಓದಲಾಗಲಿಲ್ಲ.",
  "account.permissions": "ನಿಮ್ಮ ಅನುಮತಿಗಳು",
  "account.granted": "ನೀಡಲಾಗಿದೆ",
  "account.notGranted": "ನೀಡಲಾಗಿಲ್ಲ",
  "account.noGrants": "ಈ ಬ್ಯಾಕೆಂಡ್ ನಿಮ್ಮ ಪಾತ್ರದ ಪ್ರತ್ಯೇಕ ಅನುಮತಿಗಳನ್ನು ವರದಿ ಮಾಡಿಲ್ಲ, ಆದ್ದರಿಂದ ಊಹಿಸುವ ಬದಲು ಇಲ್ಲಿ ಯಾವುದನ್ನೂ ಪಟ್ಟಿ ಮಾಡಿಲ್ಲ.",
  "account.switch": "ಡೆಮೊ ಪಾತ್ರ ಬದಲಿಸಿ",
  "account.switchNote":
    "ಡೆಮೊ ಮೋಡ್ ಮಾತ್ರ. ಪ್ರತಿ ಪಾತ್ರವೂ ಆ ಹುದ್ದೆ ಮತ್ತು ವ್ಯಾಪ್ತಿಯೊಂದಿಗೆ API ನೀಡುವ ನೈಜ ಟೋಕನ್, ಮತ್ತು ಪ್ರತಿ ಬದಲಾವಣೆಯನ್ನು ಲೆಕ್ಕಪರಿಶೋಧನಾ ದಾಖಲೆಯಲ್ಲಿ ಬರೆಯಲಾಗುತ್ತದೆ. ಜಿಲ್ಲೆ ಮತ್ತು ಠಾಣೆ ಪಾತ್ರಗಳು ಪಟ್ಟಿಗಳಲ್ಲಿ ತಮ್ಮ ವ್ಯಾಪ್ತಿಯನ್ನು ಮಾತ್ರ ನೋಡುತ್ತವೆ; ವ್ಯಾಪ್ತಿಯ ಹೊರಗಿನ ದಾಖಲೆಯನ್ನು ತೆರೆಯಬಹುದು, ಆದರೆ ಅದನ್ನು ಗುರುತಿಸಿ ಲೆಕ್ಕಪರಿಶೋಧಿಸಲಾಗುತ್ತದೆ.",
  "account.useThis": "ಈ ಪಾತ್ರ ಬಳಸಿ",
  "account.matrix": "ಪಾತ್ರಗಳು ಮತ್ತು ಅನುಮತಿಗಳು",
  "account.matrixNote": "API ಜಾರಿಗೊಳಿಸಿದಂತೆ. ನಿಮ್ಮ ಪಾತ್ರದ ಕಾಲಮ್ ಅನ್ನು ಎದ್ದು ತೋರಿಸಲಾಗಿದೆ.",
  "account.rolesMissing": "ಈ ಬ್ಯಾಕೆಂಡ್ ಪಾತ್ರಗಳ ಪಟ್ಟಿಯನ್ನು ಪ್ರಕಟಿಸುವುದಿಲ್ಲ (GET /auth/roles 404 ನೀಡಿತು).",
  "account.rolesDown": "ಪಾತ್ರಗಳ ಪಟ್ಟಿಯನ್ನು ಲೋಡ್ ಮಾಡಲಾಗಲಿಲ್ಲ — ಗುಪ್ತಚರ ಕೇಂದ್ರ ಪ್ರತಿಕ್ರಿಯಿಸಲಿಲ್ಲ.",
  "account.rolesFailed": "ಪಾತ್ರಗಳ ಪಟ್ಟಿಯನ್ನು ಲೋಡ್ ಮಾಡಲಾಗಲಿಲ್ಲ",
  "account.permission": "ಅನುಮತಿ",
  "account.you": "ನೀವು",
  "account.scope.state": "ರಾಜ್ಯವ್ಯಾಪಿ",
  "account.scope.district": "ಜಿಲ್ಲೆ",
  "account.scope.station": "ಠಾಣೆ",
  "account.watchTitle": "ಪುನರಾಪರಾಧ ನಿಗಾ ಪಟ್ಟಿ — ನೇರ",
  "account.redacted": "ಮರೆಮಾಡಲಾಗಿದೆ",
  "account.fullAccess": "ಪೂರ್ಣ ಪ್ರವೇಶ",
  "account.watchNote":
    "ಹೆಸರುಗಳಿರುವ ನಿಗಾ ಪಟ್ಟಿಯ ಅನುಮತಿ ಇಲ್ಲದ ಪಾತ್ರಕ್ಕೆ ಈ endpoint ನಿರಾಕರಿಸುವ ಬದಲು ಮರೆಮಾಡಿದ ರೂಪದಲ್ಲಿ ಉತ್ತರಿಸುತ್ತದೆ: ಎಣಿಕೆಗಳು, ಹಂತಗಳು ಮತ್ತು ವಿಧಾನ ಉಳಿಯುತ್ತವೆ; ಹೆಸರುಗಳು ಸ್ಥಿರ ಗುಪ್ತನಾಮಗಳಾಗುತ್ತವೆ ಮತ್ತು ಸಂಭವನೀಯತೆಗಳನ್ನು ತಡೆಹಿಡಿಯಲಾಗುತ್ತದೆ. 403 ನಿಯಂತ್ರಣ ಇರುವುದನ್ನೇ ಮರೆಮಾಡುತ್ತಿತ್ತು; ಇದು ಅದು ಕೆಲಸ ಮಾಡುವುದನ್ನು ತೋರಿಸುತ್ತದೆ. ಜಿಲ್ಲಾ ಅಥವಾ ರಾಜ್ಯ ಮುಖ್ಯಸ್ಥರ ಪಾತ್ರಕ್ಕೆ ಬದಲಿಸಿದರೆ ಇದೇ ಕೋಷ್ಟಕ ಬದಲಾಗುತ್ತದೆ.",
  "account.col.id": "ಗುರುತು ಸಂಖ್ಯೆ",
  "account.col.index": "ಸೂಚ್ಯಂಕ",
  "account.col.prob": "ಸಂಭವನೀಯತೆ",
  "account.col.band": "ಶ್ರೇಣಿ",
  "account.withheld": "ತಡೆಹಿಡಿಯಲಾಗಿದೆ",
  "account.watchDown": "ನಿಗಾ ಪಟ್ಟಿ ಲಭ್ಯವಿಲ್ಲ — ಬ್ಯಾಕೆಂಡ್ ಚಾಲನೆಯಲ್ಲಿದೆಯೇ?",
  "account.watchEmpty": "ನಿಗಾ ಪಟ್ಟಿಯಲ್ಲಿ ಯಾವುದೇ ಸಾಲುಗಳಿಲ್ಲ.",
  "account.demoAccounts": "ಡೆಮೊ ಪಾಸ್‌ವರ್ಡ್ ಖಾತೆಗಳು",
  "account.demoAccountsNote":
    "AUTH_ACCOUNTS ಹೊಂದಿಸದ ಕಾರಣ ಸಕ್ರಿಯವಾಗಿದೆ; ನೈಜ ವಿವರಗಳನ್ನು ಹೊಂದಿಸಿದ ಕ್ಷಣವೇ ನಿಷ್ಕ್ರಿಯಗೊಳ್ಳುತ್ತದೆ. API ಬಳಕೆದಾರ ಹೆಸರುಗಳು ಮತ್ತು ಪಾತ್ರಗಳನ್ನು ಮಾತ್ರ ನೀಡುತ್ತದೆ — ಪಾಸ್‌ವರ್ಡ್‌ಗಳು ಯೋಜನೆಯ README ಯಲ್ಲಿವೆ, ಯಾವುದೇ endpoint ಅವುಗಳನ್ನು ಹಿಂತಿರುಗಿಸುವುದಿಲ್ಲ.",
  "account.goSignIn": "ಸೈನ್ ಇನ್ ಪುಟಕ್ಕೆ ಹೋಗಿ",

  // permission names (the identifiers come from GET /auth/roles)
  "perm.view_aggregates": "ಒಟ್ಟು ಅಂಕಿಅಂಶಗಳು ಮತ್ತು ನಕ್ಷೆಗಳು",
  "perm.view_cases": "ಪ್ರಕರಣ ಕಡತಗಳು (ಎಫ್‌ಐಆರ್)",
  "perm.view_dossiers": "ಅಪರಾಧಿಗಳ ವಿವರ ಕಡತಗಳು",
  "perm.view_network": "ಸಹ-ಅಪರಾಧಿ ಜಾಲ",
  "perm.use_copilot": "ಕಾವ್ಯ ಸಹಾಯಕ",
  "perm.surveillance_view": "ಸಿಸಿಟಿವಿ ಮತ್ತು ANPR ಎಚ್ಚರಿಕೆಗಳು",
  "perm.surveillance_decide": "ಸಿಸಿಟಿವಿ ಹೊಂದಾಣಿಕೆಗಳನ್ನು ದೃಢೀಕರಿಸಿ ಅಥವಾ ತಿರಸ್ಕರಿಸಿ",
  "perm.surveillance_escalate": "ಸಿಸಿಟಿವಿ ಎಚ್ಚರಿಕೆಗಳನ್ನು ಮೇಲಧಿಕಾರಿಗಳಿಗೆ ಕಳುಹಿಸಿ",
  "perm.watchlist_names": "ಹೆಸರುಗಳಿರುವ ಪುನರಾಪರಾಧ ನಿಗಾ ಪಟ್ಟಿ",
  "perm.audit_read": "ಲೆಕ್ಕಪರಿಶೋಧನಾ ದಾಖಲೆ",
  "perm.contest_resolve": "ಅಂಕಗಳ ವಿರುದ್ಧದ ಆಕ್ಷೇಪಣೆಗಳನ್ನು ಇತ್ಯರ್ಥಪಡಿಸಿ",
  "perm.system_admin": "ವ್ಯವಸ್ಥೆ ಸೆಟ್ಟಿಂಗ್‌ಗಳು, ಸಿಂಕ್ ಮತ್ತು ದತ್ತಾಂಶ ಧಾರಣೆ",
};

const DICTIONARIES: Record<Lang, Record<string, string>> = { en: {}, kn: KN };

/** English source strings. `t(key, fallback)` also accepts an inline fallback. */
const EN_DEFAULTS: Record<string, string> = {
  "nav.overview": "Overview",
  "nav.cases": "Cases",
  "nav.investigate": "Investigate",
  "nav.map": "Map",
  "nav.network": "Network",
  "nav.analytics": "Analytics",
  "nav.models": "Models",
  "nav.registry": "Registry",
  "nav.officers": "Officers",
  "nav.lifecycle": "Lifecycle",
  "nav.pendency": "Pendency",
  "nav.dispatch": "Dispatch",
  "nav.access": "Account & roles",
  "nav.cctv": "CCTV",
  "nav.news": "Live News",
  "nav.copilot": "Copilot",
  "nav.more": "More",
  "common.skipToContent": "Skip to main content",
  "a11y.primaryNav": "Primary",
};

const _missing = new Set<string>();

/** Keys requested at runtime that have no Kannada entry — the honest to-do list. */
export function missingKeys(): string[] {
  return [..._missing].sort();
}

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, fallback?: string) => string;
}

const I18nContext = createContext<I18nValue>({
  lang: "en",
  setLang: () => {},
  t: (key, fallback) => fallback ?? EN_DEFAULTS[key] ?? key,
});

const _listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  _listeners.add(cb);
  // A second tab changing the preference should not leave this one stale.
  window.addEventListener("storage", cb);
  return () => {
    _listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

function readStoredLang(): Lang {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "kn" || stored === "en" ? stored : "en";
  } catch {
    return "en";   // private mode — English is a fine default
  }
}

/** The prerendered HTML is built without a browser, so it is always English. */
function serverLang(): Lang {
  return "en";
}

export function LangProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, readStoredLang, serverLang);

  // Keep <html lang> honest. It was hardcoded to "en", so a screen reader announced
  // Kannada text with English phonemes — which is worse than not translating at all.
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* preference simply does not persist */
    }
    // The store is the source of truth; tell the subscribers to re-read it.
    _listeners.forEach((cb) => cb());
  }, []);

  const t = useCallback(
    (key: string, fallback?: string) => {
      const english = fallback ?? EN_DEFAULTS[key] ?? key;
      if (lang === "en") return english;
      const translated = DICTIONARIES[lang][key];
      if (!translated) {
        _missing.add(key);
        return english;      // fall back to English, never to a placeholder
      }
      return translated;
    },
    [lang]
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
