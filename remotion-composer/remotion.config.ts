import {existsSync} from 'node:fs';
import {Config} from '@remotion/cli/config';

// This machine has Chrome installed and cannot reach the Remotion browser CDN
// reliably, so render with the system browser instead of downloading
// chrome-headless-shell. Other machines fall back to Remotion's bundled browser.
const systemChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

if (existsSync(systemChrome)) {
  Config.setBrowserExecutable(systemChrome);
}
