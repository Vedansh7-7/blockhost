BLOCKHOST - ONE-TIME OWNER SETUP  (about 10 minutes, free)
==========================================================

Only the owner does this, once. Friends never touch Google Cloud.

A. MAKE A GOOGLE CLOUD "DESKTOP APP" LOGIN (free, no credit card)
  1. Go to https://console.cloud.google.com/ and sign in with the Google
     account whose Drive will store the world.
  2. Top bar > project picker > NEW PROJECT. Name it "BlockHost". Create.
  3. Search bar: "Google Drive API" > open it > ENABLE.
  4. Left menu: APIs & Services > OAuth consent screen (may be called "Google Auth Platform").
       - App name: BlockHost. User support + developer email: your own.
       - Audience / User type: External.
       - Data access / Scopes: add  .../auth/drive.file
         (this lets the app see ONLY files it creates, nothing else in your Drive)
       - Audience: press PUBLISH APP (switch to "In production").
         IMPORTANT: if you leave it in "Testing", Google expires the login
         after 7 days and hosting stops working.
         Google will NOT ask for verification, because drive.file is a basic scope.
  5. Clients (or Credentials) > CREATE CLIENT / CREATE CREDENTIALS > OAuth client ID
       - Application type: Desktop app. Name: BlockHost. Create.
       - Copy the Client ID and Client secret.

B. CREATE THE GROUP
  1. Open BlockHost.exe > CREATE GROUP.
  2. Paste the Client ID and Client secret, choose a group name, the Minecraft
     version (26.3), and a group password (8+ characters).
  3. Press SIGN IN & CREATE. A browser tab opens. Sign in, and if Google shows
     "Google hasn't verified this app", click Advanced > Go to BlockHost.
     (It's your own app. That warning is normal for personal projects.)
  4. Save the .bhgroup file it offers.

C. SHARE WITH FRIENDS
  - Send them BlockHost.exe and the .bhgroup file (e.g. in a Drive folder).
  - Send the group password SEPARATELY (e.g. by message).
  - They open BlockHost.exe > JOIN GROUP > pick the file > type the password.
  - The first time someone hosts, Windows asks for admin permission once (firewall),
    and BlockHost offers to install Java if it's missing.

SECURITY NOTES
  - The world in Drive is encrypted (AES-256). Only BlockHost with the group
    file + password can open it. A leaked Drive link alone is useless.
  - The .bhgroup file is also encrypted with the password.
  - Anyone with the file AND the password can host and change the world.
    If someone leaves the group: create a new group and resend.
  - To revoke everything: https://myaccount.google.com/permissions > BlockHost > Remove access.
  - In Drive you'll see a folder "BlockHost - <name>" with state.json and up to two
    world-*.bhw files. Don't edit them by hand.
