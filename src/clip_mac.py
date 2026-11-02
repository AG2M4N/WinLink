"""macOS clipboard: text and images, via NSPasteboard."""

from AppKit import (
    NSBitmapImageRep,
    NSPasteboard,
    NSPasteboardTypePNG,
    NSPasteboardTypeString,
    NSPasteboardTypeTIFF,
)

PNG, BMP = 4, 1          # NSBitmapImageFileType values
FILE_URL = "public.file-url"


class Clipboard:
    def __init__(self, _window=None):
        self.pb = NSPasteboard.generalPasteboard()

    def seq(self):
        return self.pb.changeCount()

    def read(self):
        """{"kind": "text", "text"} / {"kind": "image", "png", "bmp"} / None."""
        types = list(self.pb.types() or [])
        if FILE_URL in types:          # files copied in Finder: use the Files section instead
            return None
        text = self.pb.stringForType_(NSPasteboardTypeString)
        if text:
            return {"kind": "text", "text": str(text)}
        data = self.pb.dataForType_(NSPasteboardTypePNG) or self.pb.dataForType_(NSPasteboardTypeTIFF)
        if data:
            rep = NSBitmapImageRep.imageRepWithData_(data)
            if rep is not None:
                return {"kind": "image",
                        "png": bytes(rep.representationUsingType_properties_(PNG, {})),
                        "bmp": bytes(rep.representationUsingType_properties_(BMP, {}))}
        return None

    def write(self, item):
        """Put an item on the clipboard; returns the new change count."""
        if item["kind"] == "text":
            self.pb.clearContents()
            self.pb.setString_forType_(item["text"].replace("\r\n", "\n"), NSPasteboardTypeString)
        else:
            rep = NSBitmapImageRep.imageRepWithData_(item.get("png") or item.get("bmp"))
            if rep is None:
                return self.seq()
            self.pb.clearContents()
            self.pb.declareTypes_owner_([NSPasteboardTypePNG, NSPasteboardTypeTIFF], None)
            self.pb.setData_forType_(rep.representationUsingType_properties_(PNG, {}), NSPasteboardTypePNG)
            self.pb.setData_forType_(rep.TIFFRepresentation(), NSPasteboardTypeTIFF)
        return self.seq()
