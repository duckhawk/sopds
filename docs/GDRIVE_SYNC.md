# Google Drive reading-position sync (Moon+ Reader)

Each user connects their **own** Google Drive. SOPDS reads/writes a per-book
position file compatible with Moon+ Reader:

```
<SOPDS_GDRIVE_PATH>/<title> - <author full_name>.<format>.zip.po
```

Default path `Books/.Moon+/Cache` (configurable in the admin via
`SOPDS_GDRIVE_PATH`). File content is the Moon+ Reader format
`<timestamp_ms>*<chapter>@<volume>#<char_offset>:<percent>%`; only the
percentage (computed over book text characters) is synced.

Sync is two-way, "newer wins" by the Drive file `modifiedTime` vs the local
`bookshelf.position_time`:

- **On opening a book** the reader pulls the position; Drive wins if its file is
  newer, otherwise the exact local position is restored.
- **On pause/scroll** the reader pushes the current percentage; the server
  mirrors it to the Drive `.po`.

If nothing is configured or connected, the reader works exactly as before.

## One-time Google Cloud setup (admin)

1. Create (or reuse) a project at <https://console.cloud.google.com/>.
2. **APIs & Services → Enable APIs → Google Drive API** → enable.
3. **OAuth consent screen**: User type *External*, publishing status
   **Testing**. Add each user's Google account under **Test users**
   (Testing mode is required because we use the restricted `drive` scope and the
   app is not going through Google verification).
4. **Credentials → Create credentials → OAuth client ID**, type **Web
   application**. Add **Authorized redirect URIs** for every environment:
   - `https://<prod-host>/web/gdrive/callback/`
   - `https://<dev-host>/web/gdrive/callback/`
5. Copy the **Client ID** and **Client secret**.

## Wiring the credentials

The app reads the client from environment variables
`GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (empty → feature
disabled). In the k8s chart they come from `sopds.google_oauth.*`; put the real
values in the werf secret:

```bash
werf helm secret values edit .helm/secret-values.yaml
# sopds:
#   google_oauth:
#     client_id: "....apps.googleusercontent.com"
#     client_secret: "...."
```

## Using it

In the web reader each user clicks **Connect Google Drive**, authorizes their
account, and positions start syncing. **Drive: on** (disconnect) shows the
connected state. The scope requested is full `drive` because two-way sync must
write Moon+ Reader's existing files.

## Notes / limitations

- The `.po` filename must match how Moon+ Reader names the book file. This
  build assumes `"<title> - <author>.<format>.zip.po"` (matches SOPDS-served
  zipped FB2). Different naming on the device won't be matched.
- The internal `chapter`/`char_offset` fields are Moon Reader's own; we only
  round-trip the percentage, so positions land near (not pixel-exact) the mark.
- Restricted `drive` scope in Testing mode shows an "unverified app" warning and
  is limited to listed test users — fine for a personal/self-hosted instance.
