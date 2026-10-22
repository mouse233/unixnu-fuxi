/*
 * Stage 2a stubs for the legacy display hooks.
 *
 * bsd_init references native_legacy_display_probe() and
 * native_legacy_display_late_probe() unconditionally. The real implementations
 * come from build-native-legacy-display.py, which runs
 * audit-mobile-graphics-legacy.py; that audit needs a prelink candidate
 * (build/native-prelink/native-owner-legacy-display-*-candidate.macho) that
 * cannot exist before the kernel links, so the archive step cannot run on a
 * first build.
 *
 * Linking these no-ops lets the diagnostic kernel link. Its panic screen is
 * the visible output while the full display/boot progress stack is absent.
 * Replace the display hooks once that stack is available.
 */

void
native_legacy_display_probe (
  void
  )
{
}

void
native_legacy_display_late_probe (
  void
  )
{
}

/* NativeUfs has an optional passive observer. A static kernel link still
 * requires a definition for its weak reference. The real BootProgress
 * implementation overrides this fallback when that stack is selected. */
void __attribute__((weak))
boot_progress_poll (void)
{
}
