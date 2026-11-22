# Legal guardrails (read before adding features)

This is a free, non-commercial, open-source fan project. These rules keep it
that way. They are practical guidelines, not legal advice.

## Always
- Keep the unofficial disclaimer in the footer of every web page and in the README:
  > This website is unofficial and is not associated in any way with the Formula 1
  > companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP,
  > GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.
- Show drivers by **name and team colour** only.
- Say "probably" / "likely" for anything we infer (battery state is never measured).

## Never
- Put "F1", "Formula", "GP", "Grand Prix" or a team name in the **project name,
  domain, app name, logo or social media handle**. (Using them in normal sentences
  to describe a race is fine.)
- Use driver headshots, race photos, broadcast video, team logos or the F1 logo.
  (OpenF1 returns `headshot_url` — we deliberately ignore it.)
- Commit raw or generated race data to git, or offer bulk data downloads.
  Users run the pipeline themselves.
- Charge money, add paywalls or ad networks. Optional donations, if ever added,
  must be for the analysis/writing — never for access to data.

## Why
- Formula One Licensing actively enforces its trade marks against fan apps and
  creators (e.g. an open-source F1TV client was removed from the Microsoft Store
  after a complaint).
- OpenF1 data is for non-commercial use.
- In Germany, using a person's image commercially without consent violates
  personality rights (§ 22 KUG).

If the project ever wants to make money, talk to a trade-mark lawyer first.
