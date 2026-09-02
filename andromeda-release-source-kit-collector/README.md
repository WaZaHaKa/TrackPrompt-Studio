# Andromeda release source-kit collector

Extract this folder anywhere, then double-click:

`RUN-COLLECT-ANDROMEDA-SOURCE-KIT.cmd`

The collector reads the TrackPrompt Studio checkout at:

`C:\Users\theon\GitHub\TrackPrompt-Studio`

It creates a timestamped ZIP on the Desktop containing only the source,
contracts, tests, production JSON evidence, render profiles, Git metadata, and
latest forensic metadata needed to build an exact release-closure/render-start
orchestrator.

It excludes `.env`, credentials, audio, `.blend` files, image sequences,
previews, final media, databases, model weights, and runtime logs.
