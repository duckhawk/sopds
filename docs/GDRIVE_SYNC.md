# Google Drive reading-position sync (Moon+ Reader)

Each user connects their **own** Google Drive and picks the folder that holds
Moon+ Reader's position files. SOPDS then reads/writes a per-book position file
compatible with Moon+ Reader:

```text
<picked folder>/<title> - <author full_name>.<format>.zip.po
```

Content is the Moon+ Reader format
`<timestamp_ms>*<chapter>@<volume>#<char_offset>:<percent>%`; only the
percentage (computed over book text characters) is synced.

## Least-privilege access

The app uses the **`drive.file`** scope: it can only touch files it created or
files/folders the user explicitly grants through the **Google Picker**. The user
picks their Moon+ Reader cache folder once (usually `Books/.Moon+/Cache`), and
the app gets access to **that folder only** — nothing else in their Drive.
`drive.file` is not a restricted scope, so Google app verification is not
required.

Sync is two-way, "newer wins" by the Drive file `modifiedTime` vs the local
`bookshelf.position_time`:

- **On opening a book** the reader pulls the position; Drive wins if its file is
  newer, otherwise the exact local position is restored.
- **On pause/scroll** the reader pushes the current percentage (throttled); the
  server mirrors it to the Drive `.po`.

If nothing is configured/connected, the reader works exactly as before.

## One-time Google Cloud setup (admin)

1. Create/reuse a project at <https://console.cloud.google.com/>.
2. **APIs & Services → Enable APIs**: enable **Google Drive API** and
   **Google Picker API**.
3. **OAuth consent screen**: User type *External*. Scope `.../auth/drive.file`
   is non-sensitive, so verification is not required; Testing mode is fine (add
   your accounts as test users).
4. **Credentials → OAuth client ID**, type **Web application**. Authorized
   redirect URIs (one per environment):
   - `https://<prod-host>/web/gdrive/callback/`
   - `https://<dev-host>/web/gdrive/callback/`
5. **Credentials → API key** (used by the Picker). Optionally restrict it to the
   Picker API and to your host as an HTTP referrer.
6. Copy the **Client ID**, **Client secret** and **API key**.

## Wiring the credentials

The app reads them from environment variables (empty → feature disabled):
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_API_KEY`. In the
k8s chart they come from `sopds.google_oauth.*`; put the real values in the werf
secret:

```bash
werf helm secret values edit .helm/secret-values.yaml
# sopds:
#   google_oauth:
#     client_id: "....apps.googleusercontent.com"
#     client_secret: "GOCSPX-..."
#     api_key: "AIza..."
```

## Using it

1. Open **`/web/gdrive/`** (or the "Setup Google Drive" button in the reader).
2. **Connect Google Drive** → authorize your account.
3. **Select Moon+ Reader folder** → in the Picker, browse to and select the
   folder with the `.po` files (usually `Books/.Moon+/Cache`).
4. Positions now sync while reading.

## Notes / limitations

- The `.po` filename must match how Moon+ Reader names the book file. This build
  assumes `"<title> - <author>.<format>.zip.po"` (matches SOPDS-served zipped
  FB2). Different naming on the device won't be matched.
- We only round-trip the percentage (chapter/char_offset are Moon Reader's own),
  so positions land near — not pixel-exact — the mark.
- Reading files that Moon Reader (another app) created, inside the folder you
  grant via Picker, relies on `drive.file` folder-grant behaviour. If a future
  Google change stops exposing other-apps' files under a picked folder, the read
  side would need the broader `drive.readonly` scope.
