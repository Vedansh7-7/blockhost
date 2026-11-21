# Owner setup (one time, ~15 minutes, free)

Only the group **owner** does this. Friends only need the app, the `.bhgroup` file and the password.

## 1. Google Cloud project
1. Open <https://console.cloud.google.com/> with the Google account whose Drive will store the world.
2. Project picker → **New project** → name it `BlockHost` → **Create**.
3. Search **Google Drive API** → **Enable**.

## 2. Homepage and privacy policy (Google requires them to publish)
Google won't let you publish the app without a homepage URL and a privacy-policy URL.
The [`site/`](../site) folder has both pages, ready to use. Deploy that folder to any free static host:

| Host | Command / steps |
|---|---|
| Vercel | `cd site && vercel deploy --prod` |
| Netlify | drag the `site` folder onto <https://app.netlify.com/drop> |
| GitHub Pages | push `site/` to a public repo → Settings → Pages |

You'll get something like `https://your-name.vercel.app` and `https://your-name.vercel.app/privacy.html`.

## 3. OAuth consent screen (Google Auth Platform)
1. **Branding**:
   - App name: `BlockHost`
   - Support email: yours
   - **Application home page** and **Application privacy policy link**: your two URLs from step 2
   - **Authorized domains**: your host's domain, e.g. `your-name.vercel.app`
2. **Audience**: External.
3. **Data access**: add the scope `.../auth/drive.file`.
4. **Audience** → **Publish app** → confirm. The status must say **In production**.

> ⚠️ If the app stays in **Testing**, Google expires the login after **7 days** and hosting stops working.
> You do **not** need Google's verification: `drive.file` is a non-sensitive scope.

## 4. OAuth client
**Clients** → **Create client** → type **Desktop app** → name `BlockHost` → **Create**.
Copy the **Client ID** and **Client secret**. Keep them private and never commit them; the repo already ignores `client_secret*.json`.

## 5. Create the group
1. Open BlockHost → **CREATE GROUP**.
2. Paste the Client ID and secret, then set a group name, the Minecraft version and a group password (8+ characters).
   - Leave **online mode** ticked if everyone has an official Minecraft account.
3. **SIGN IN & CREATE**. When Google warns *"Google hasn't verified this app"*: **Advanced → Go to BlockHost**. It's your own app.
4. Save the `.bhgroup` file.

## 6. Invite friends
- Send them **BlockHost** (from Releases) and the **`.bhgroup` file**.
- Send the **password separately**, for example as a different message.
- They click **JOIN GROUP**, pick the file and enter the password.

## Revoking access
- Remove a member: create a new group and share it only with the people who should stay.
- Remove everything: <https://myaccount.google.com/permissions> → BlockHost → *Remove access*, then delete the `BlockHost - …` folder in Drive.
