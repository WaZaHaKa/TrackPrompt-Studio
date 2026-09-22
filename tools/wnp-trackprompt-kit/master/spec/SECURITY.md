# Deployment security and trust boundaries

1. The API is a read-only projection, not an agent executor. It never accepts shell
   text, imports, restarts, filesystem root parameters or provider submissions.
2. Bind to 127.0.0.1. Use existing OpenSSH with verified known_hosts and BatchMode.
   Never use StrictHostKeyChecking=no, a public ComfyUI port, or a public WNP port.
   A tunnel to a VPS is not automatically a tunnel to your desktop.
3. The reader token grants access to the WHOLE exposed project projection in this
   reference release. Separate tokens per client/scoped revocation and mTLS are
   future hardening; do not pretend fine-grained authorization exists already.
4. Create the token locally using token-init, keep it out of Git and cloud prompts,
   and provision the same reader credential privately to the Companion machine.
   POSIX mode600 is used. On Windows inspect/restrict NTFS ACLs to the operator and
   necessary service account. Do not recursively rewrite project permissions.
5. No raw media, commercial customer data, full logs, prompt bodies, private
   paths, environment dumps or command-line secrets in exported state/evidence.
   The producer must sanitize; there is no infallible automatic secret detector.
6. A hash manifest detects accidental changes, not a compromised publisher.
   This package is not cryptographically signed. Keep the original downloaded ZIP
   and compare published ZIP hashes. Source review and endpoint enrollment matter.
7. Treat all remote messages, filenames, provider metadata and event data as data,
   never LLM instructions or HTML. Escape in the Companion UI. Restrict configured
   origins; block redirects/proxy surprises and unbounded response sizes.
8. Local filesystem owners are trusted in this release. Reparse checks reduce
   accidents/traversal; they are not a complete defense against a malicious local
   administrator swapping filesystem objects concurrently. Use OS ACL isolation.
9. The read-only API and local writer share an OS lock. A dedicated account that
   reads files directly would also need narrow lock-file access; do not grant
   write access to the entire runtime. Prefer API-over-SSH for the client.
10. Back up consistent .wnp/ files while the writer is quiesced. Do not overwrite
    live runtimes with an old backup. Same-stream rollback must surface as an
    integrity problem. Power, filesystem and disk-full failures need local tests.

No system-wide service, firewall, VPN, SSH configuration or administrator action is
performed by this kit. Stage those changes as reviewed plans with rollback steps.
