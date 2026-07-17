# Cadence

A self-hosted, single-track music request service. Search for one song, request it, an admin
approves it, and Cadence fetches exactly that track, nothing else.

Every other tool in this space (Lidarr, Ombi, Headphones) is built around the artist or album
as the unit of work: monitor an artist and you get their whole discography. Cadence exists
because sometimes you just want one song.

Status: early build, following [MISSION.md](MISSION.md). Not yet functional. See
[REPORT.md](REPORT.md) once Phase 1 is complete for what has actually been built.

## What it does

- Search for a track across multiple sources at once (Apple/iTunes, MusicBrainz, Spotify,
  your own Prowlarr indexers), shown as separate tabs so you can pick the right result with
  correct album art.
- Request it. An admin approves or rejects from a queue.
- On approval, Cadence tries your Prowlarr indexers first, then falls back to Soulseek (via
  slskd) if nothing suitable turns up, grabs the single track, files and tags it.
- No album, no artist, no library scan. One song in, one song done.

## Status

Phase 1 (backend + web client) is in progress. Native apps (Windows, macOS, iOS, Android) are
later, separate phases, each built against Phase 1's API once it is stable.

## License

MIT, see [LICENSE](LICENSE).
