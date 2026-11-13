CoIoED Hearing Schedule Update - Windows (Offline) - 13 Nov 2026
================================================================
This installer UPDATES AN EXISTING CoIoED Python installation. It is NOT a fresh full installer.
It contains no confidential case records, database or encryption keys.

INSTALL:
1. On your already-authorized Windows computer, CLOSE CoIoED and the console window.
2. Make a separate approved offline backup of your database and attachments BEFORE updating.
3. Extract this ZIP to a folder OUTSIDE your existing CoIoED installation.
4. Double-click INSTALL_HEARING_UPDATE.bat; enter the full path to the EXISTING CoIoED folder containing app.py.
5. The updater backs up the two modified Python source files and performs synthetic smoke tests. It does not modify coioed.db, uploads, or private data.
6. Re-launch your original LAUNCH_COIOED.bat and sign in. Test with fabricated draft notices.

FEATURES:
- Move complete draft hearing days between editable draft notices with optional new hearing date.
- Notice list titles now display earliest and latest scheduled date, e.g. 11th November 2026 to 13th November 2026. Notice identifiers remain stable internally.
- Print Day link beside each day opens the official single-day PDF using your existing schedule renderer.
- Clubbed 2-3 case rows preserve each person's own name AND parentage on separate subrows in printed PDF, HTML, and editable XLSX; combined-row editor displays and links each member separately.
- Protection against duplicate day dates and moving from/to locked approved notices.

ROLLBACK:
Double-click ROLLBACK_HEARING_UPDATE.bat; enter the same CoIoED installation path.
The latest backed-up source files will be restored. Database changes are not involved.

LIMITATIONS:
- This is a non-production-accredited offline pilot. Requires authorized IT security review.
- Print Day opens the PDF inline; use the browser PDF print icon for actual printing.
- Source updater does not modify/create a database and cannot repair previously inconsistent records.
- If the CoIoED installation uses different schema/source revisions, the updater refuses mismatches; test before office use.