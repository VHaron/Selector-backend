# ChampionPoker authentication flow — investigation notes

Date: 2026-11-25
Scope: isolated iPoker parser test branch only. No production changes.

## Confirmed from HAR and client JavaScript

- Poker client page loads `PokerRender.bundle.js` and the login integration script:
  `https://login.championpoker.com/jswrapper/integration.js.php?casino=championpoker`.
- The integration script exposes `iapiLoginAndGetTempToken(...)`. Its documented flow calls `LoginAndGetTempToken.php` with casino, real-mode, service type, optional system ID, and login form fields.
- The HAR captured a different request for an already-authenticated browser session:
  `POST https://login.championpoker.com/GetTemporaryAuthenticationToken.php`
  with query parameters including `casinoname=championpoker`, `serviceType=GamePlay`, `systemId=18`, `realMode=1`, and client platform details.
- Its form body included `requestType=GetTemporaryAuthenticationToken`, `userId`, `token`, `username`, and `responseType=json`.
- The successful JSON response contains `sessionToken.sessionToken`, plus issuer system ID, creation/expiration timestamps, username, user ID, and verified redirect origin.
- The client-side Socket.IO bootstrap message observed in DevTools uses `classId:60108` and fields `usernameOrNickname`, `tempToken`, client version/platform, and device metadata.
- The next server message uses `classId:60109` and returns the authenticated poker `token`, `sessionId`, currency, and `errorCode:0`.

## Interpretation

The likely bridge is that the temporary session token returned by the login integration is passed to the poker WebSocket login as `tempToken`. This is strongly suggested by the matching flow and field names, but the HAR's WebSocket frames are not included, so the exact value-to-value mapping has not yet been independently verified in one capture.

Crucially, the HAR's `GetTemporaryAuthenticationToken.php` request already had an authenticated web-session `userId` and `token`. It does **not** show how those values were initially obtained. Therefore the current evidence does not establish that a clean Railway worker can authenticate without first completing the ChampionPoker website login flow.

## Security

- Never commit or log `userId`, web-session `token`, `tempToken`, poker session `token`, cookies, or raw HAR request/response bodies.
- Previously pasted tokens must be treated as exposed and revoked by logging out / logging in again.
- Any future collector must keep credentials and short-lived tokens in Railway secrets, redact them from logs, and use the user's own authorized account only.

## Next engineering steps

1. Inspect the login integration's exact `LoginAndGetTempToken.php` request construction and callback/response handling.
2. Determine whether a supported username/password or external-token login can be initiated server-side, including MFA/challenge handling, without reusing browser cookies.
3. Only after that, implement a separate authenticated session bootstrap module. Keep it independent from the WebSocket tournament parser.
4. Connect to `wss://schannel.ipoker.com/socket.io/?EIO=3&transport=websocket`, perform Engine.IO/Socket.IO handshake, send the 60108 login message using a fresh temporary token, require a successful 60109 response, then subscribe to tournament filters.
5. Do not enable a scheduled Railway worker until authentication, token refresh/expiry, reconnection, and redaction are tested in the isolated environment.

## Current status

- WebSocket endpoint and Engine.IO v3 handshake: confirmed.
- Poker WebSocket login message structure: confirmed.
- Temporary-token issuance endpoint and its authenticated-session inputs: confirmed from HAR.
- How the browser initially obtains the web-session `userId` and `token`: unresolved.
- Fully unattended server-side login: not yet proven.
