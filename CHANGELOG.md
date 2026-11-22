# Changelog

## 0.3.0 (2026-11-22)
- New WORLD screen: difficulty, default game mode, PvP, keep items on death, hardcore,
  server name, max players, spawn protection, view distance, whitelist. Stored with the group, applies to every host.
- START NEW WORLD with your own seed and world type (Normal, Large Biomes, Amplified, Flat)
- The previous world is kept as the OLD WORLD and can be switched back at any time
- Shows the current world's seed
- Fix: Minecraft 26.3 turns the whitelist on by default; BlockHost now controls it explicitly

## 0.2.0 (2026-11-21)
- macOS support (Apple Silicon and Intel builds via CI)
- Minecraft EULA prompt before the first start
- Per-group "online mode" option (on by default for new groups)
- Server startup failures (corrupt world, busy port, low memory) are detected in seconds

## 0.1.0
- First version: Drive-backed lock with heartbeat, takeover and fencing; encrypted world saves; pixel UI
