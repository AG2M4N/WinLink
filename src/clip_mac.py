"""macOS clipboard: text, images and copied files, via NSPasteboard.

Files from another computer travel only when they're pasted. macOS can't wait for that (it asks for
a file's location the moment it's copied), so the clipboard gets a WinLink marker instead, and
watch_paste() notices ⌘V in Finder: the files then come straight into that Finder folder.
"""

import subprocess

from AppKit import (
    NSBitmapImageRep,
    NSEvent,
    NSImage,
    NSPasteboard,
    NSPasteboardTypePNG,
    NSPasteboardTypeString,
    NSPasteboardTypeTIFF,
    NSPasteboardURLReadingFileURLsOnlyKey,
    NSURL,
    NSWorkspace,
)

PNG = 4                  # NSBitmapImageFileType value
FILE_URL = "public.file-url"
REMOTE = "com.winlink.remote-files"     # files copied on another computer: holds their offer id
KEY_DOWN = 1 << 10                      # NSEventMaskKeyDown
COMMAND, SHIFT, OPTION, CONTROL = 1 << 20, 1 << 17, 1 << 19, 1 << 18
KEY_V = 9


class Clipboard:
    def __init__(self, _window=None):
        self.pb = NSPasteboard.generalPasteboard()

    def seq(self):
        return self.pb.changeCount()

    def read(self):
        """{"kind": "text", "text"} / {"kind": "image", "png", "bmp"} / {"kind": "files", "paths"} / None."""
        types = list(self.pb.types() or [])
        if REMOTE in types:            # our own marker for another computer's files
            return None
        if FILE_URL in types:          # files copied in Finder: only their paths (sent when pasted)
            urls = self.pb.readObjectsForClasses_options_([NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True})
            paths = [str(u.path()) for u in (urls or []) if u.path()]
            return {"kind": "files", "paths": paths} if paths else None
        text = self.pb.stringForType_(NSPasteboardTypeString)
        if text:
            return {"kind": "text", "text": str(text)}
        # A photo copied on an iPhone (Universal Clipboard) is usually JPEG or HEIC, not PNG or TIFF
        data = None
        for t in (NSPasteboardTypePNG, NSPasteboardTypeTIFF, "public.jpeg", "public.heic", "public.heif"):
            if t in types:
                data = self.pb.dataForType_(t)
                if data:
                    break
        if not data:                   # any other image type macOS can read
            img = NSImage.alloc().initWithPasteboard_(self.pb) if NSImage.canInitWithPasteboard_(self.pb) else None
            data = img.TIFFRepresentation() if img is not None else None
        if data:
            rep = NSBitmapImageRep.imageRepWithData_(data)
            if rep is not None:
                # PNG only: the other computers make a bitmap themselves if they need one, and a photo's
                # BMP is huge (about 48 MB for an iPhone's 12 megapixels).
                return {"kind": "image", "png": bytes(rep.representationUsingType_properties_(PNG, {}))}
        return None

    def remote_id(self):
        """The offer id of another computer's files on the clipboard, or None."""
        if REMOTE not in list(self.pb.types() or []):
            return None
        value = self.pb.stringForType_(REMOTE)
        return str(value) if value else None

    def write(self, item):
        """Put an item on the clipboard; returns the new change count."""
        if item["kind"] == "text":
            self.pb.clearContents()
            self.pb.setString_forType_(item["text"].replace("\r\n", "\n"), NSPasteboardTypeString)
        elif item["kind"] == "files":
            self.pb.clearContents()
            if item.get("paths"):          # files that are here
                self.pb.writeObjects_([NSURL.fileURLWithPath_(p) for p in item["paths"]])
            else:                          # another computer's: a marker until they're pasted
                self.pb.declareTypes_owner_([REMOTE], None)
                self.pb.setString_forType_(item["id"], REMOTE)
        else:
            rep = NSBitmapImageRep.imageRepWithData_(item.get("png") or item.get("bmp"))
            if rep is None:
                return self.seq()
            self.pb.clearContents()
            self.pb.declareTypes_owner_([NSPasteboardTypePNG, NSPasteboardTypeTIFF], None)
            self.pb.setData_forType_(rep.representationUsingType_properties_(PNG, {}), NSPasteboardTypePNG)
            self.pb.setData_forType_(rep.TIFFRepresentation(), NSPasteboardTypeTIFF)
        return self.seq()


def watch_paste(on_paste):
    """Call on_paste(offer id) when ⌘V is pressed in Finder while another computer's files are on the
    clipboard. Needs WinLink allowed under Accessibility (as keyboard sharing does); call on the main thread."""
    pb = NSPasteboard.generalPasteboard()

    def key(event):
        try:
            flags = event.modifierFlags() & (COMMAND | SHIFT | OPTION | CONTROL)
            if event.keyCode() != KEY_V or flags != COMMAND:
                return
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is None or front.bundleIdentifier() != "com.apple.finder":
                return
            if REMOTE in list(pb.types() or []):
                oid = pb.stringForType_(REMOTE)
                if oid:
                    on_paste(str(oid))
        except Exception:  # noqa: BLE001  (never let a key press raise into AppKit)
            pass
    return NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEY_DOWN, key)


def finder_folder():
    """The folder Finder pastes into (its front window, or the Desktop)."""
    r = subprocess.run(["osascript", "-e", 'tell application "Finder" to POSIX path of (insertion location as alias)'],
                       capture_output=True, text=True, timeout=10)
    path = r.stdout.strip()
    if r.returncode != 0 or not path:
        raise OSError(r.stderr.strip()[:200] or "Finder didn't say which folder is open")
    return path
