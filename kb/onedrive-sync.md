---
id: onedrive-sync
title: OneDrive not syncing or stuck
category: software
os: any
keywords: onedrive, sync, syncing, not syncing, sync pending, processing changes, stuck, files not uploading, red x, cloud icon, sharepoint sync, can't be synced
sources:
  - https://support.microsoft.com/en-us/onedrive/repair-sync-connections-in-onedrive-for-work-or-school
  - https://support.microsoft.com/en-us/onedrive/troubleshoot-onedrive-sync-issues-stuck-on-processing-changes
  - https://support.microsoft.com/en-us/office/onedrive-shows-this-file-can-t-be-synced-54f6e0d2-41d3-496a-826e-964e59333d7e
  - https://support.microsoft.com/en-us/onedrive/fix-onedrive-sync-problems-on-mac
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask what the OneDrive icon shows near the clock or menu bar, and whether there is an error message or a file name. Note it for the ticket.
2. If it says Processing changes, ask them to wait a while. Large folders take time, and it often just needs patience.
3. If a particular file will not sync, ask whether that file is open in an app. A file that is open cannot sync; ask them to close it.
4. If they recently changed their password, OneDrive may ask them to sign in again. Ask them to sign in when prompted.
5. Ask them to check they have an internet connection and that their account has not run out of storage space.
6. Ask them to restart the computer and see whether syncing resumes.

## Escalate if
Still stuck after a restart; the same error repeats; files appear missing; or many people have the problem (set scope to several users). Do not ask the caller to unlink, reset or delete anything by phone.

## Technician notes
From Microsoft's guides: pause sync for two hours, move one file out of the OneDrive folder, resume, and move it back; unlink and re-link the account (no data lost, local folders are disconnected for a short while); reset OneDrive (triggers a full sync, no files are lost); for work or school accounts the Repair option in the taskbar menu fixes many errors, and the computer must not restart while it runs; for "This file can't be synced" check the file's Read and Write permissions; a "Sync pending" with no visible file can be a hidden file; "Unable to sync shortcut" is fixed by removing the shortcut to the subfolder, not the folder; the Mac steps differ from Windows.
