# Docker rollback contract — procedure not yet release-verified

The existing production MA/dashboard, native audio stack, accounts/queues and private
receipts are the rollback baseline. Do not remove them while qualifying the new
stack. This document is a required package input, not approval to cut over or a
claim that rollback was exercised.

Before any M5 activation, freeze matching source/image/config/host-bootstrap input
identities and retain private copies of original bytes, ownership and service state.
Keep credentials behind existing server-side references. Do not copy private state
into a public PR, source archive or generic log. Map exact affected services and
containers; unrelated projects are outside the rollback operation.

The release owner must demonstrate this bounded sequence:

1. Stop/revoke candidate output independently of chat/model and verify actual owned
   PCM/DSP children have exited. Silence-only readback is not physical silence proof.
2. Stop only the candidate audio owners and its namespace/lease helper. Verify no
   candidate DAC owner remains before any old owner is enabled. Release only the
   candidate DHCP lease/namespace; restore no guessed address or host-wide firewall.
3. Restore narrowly changed host device/permission/config bytes from bootstrap's
   recorded private rollback metadata after checking exact identity and ownership.
   Do not perform broad chmod/chown, delete accounts/queues or replace shared state.
4. Restore the identical recorded old MA/application/backend/transport source,
   image/config identities and matching mounts/accounts/queues; do not migrate or
   rewrite old MA state in place for a candidate test. No automatic music, stale
   grants, volume increases or forced
   seeks. Returning to the old stack must remain quiet until genuine ordinary user
   Spotify action; test shutdown must not disable enrolled everyday use.
5. Verify existing authenticated SSH MCP/manual UI, readiness and retained room/
   profile/calibration state. Check restart/no-autoplay and report any prerequisite
   rather than inventing rollback success from a healthy HTTP listener.

Exact activation/restore commands and their target receipts remain M5 work: the
audio owner must bind these steps to its final device/bootstrap and DHCP carrier
implementation. A connect-only guard or racy helper is not a namespace boundary.
The old frozen source checkpoint221e7a7 remains historical; revision2 patch/network
inputs require a new inventory/freeze and only affected gates, without relabeling
old proof. No rollback command is run by reading this document.
