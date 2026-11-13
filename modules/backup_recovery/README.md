# CoIoED Backup and Disaster Recovery — v13.3 Prototype

This is an **offline SQLite backup utility** that has not been integrated with the full case-management application or deployed on a Commission workstation/server.

## Capabilities
- Consistent SQLite snapshot via SQLite online backup API and database integrity check
- Attachment manifest with SHA-256 checksums
- AES-256-GCM encrypted backup archives with externally stored 256-bit key
- Primary and secondary directory replicas, verified before retention pruning
- Archive authentication, integrity verification, recovery into **new isolated staging directory** (never directly overwrites live database)
- Snapshot/nightly/weekly/preupdate backup tiers; local log and health check
- Windows Task Scheduler example (15-minute snapshots, nightly and weekly backups, daily health task)

## Setup (synthetic records only)
1. Use Python 3.11+ and install `pip install -r requirements.txt`.
2. Copy `backup_config.example.json` to `backup_config.json`; use absolute paths and physically distinct backup volumes.
3. Run `python backup_manager.py --config backup_config.json init-key` **once**. Securely store recovery key separately offline. Do not put it in source control.
4. Run `python backup_manager.py --config backup_config.json backup --tier snapshot`.
5. Run `python backup_manager.py --config backup_config.json health`.
6. Run `python backup_manager.py --config backup_config.json restore-stage --archive PATH_TO_CBK --destination EMPTY_NEW_DIRECTORY`.
7. Run `python -m unittest discover -s tests -v`.

## Limitations and security warnings
- This is **not a production-ready design** for sensitive government data. Commission IT approval, ACL review and independent security testing are mandatory before use.
- **Important:** SQLite snapshot and filesystem attachments are not captured atomically together. Application writes/attachment updates must be coordinated or paused during backup to prevent inconsistent backups.
- Retention by tier is based on archive counts; verify storage and retention policy before deployment.
- Encryption keys must be backed up separately and protected with restrictive ACLs. Losing the key means losing all corresponding backups.
- If primary and secondary paths point to the same physical device, they do not provide physical redundancy.
- The scheduled-task sample runs under SYSTEM, may not have network-share access, and its recurring trigger has a finite duration. Adapt and test for the actual Windows host.
- Offsite offline/immutable copy rotation, alert integration, safe key rotation, comprehensive disaster drills, and a production-safe quiesce mechanism remain outstanding.
- Do **not** upload live records, backups, keys, or real configuration to GitHub.
