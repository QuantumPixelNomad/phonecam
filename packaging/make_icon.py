"""Render the app's drawn camera icon to a Windows .ico file (used by the build)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon  # noqa: E402

QIcon.fromTheme = staticmethod(lambda *a: QIcon())   # force the drawn icon
app = QGuiApplication(sys.argv)
import phonecam  # noqa: E402

png = sys.argv[1] + ".png"
phonecam.app_icon().pixmap(256, 256).save(png)
Image.open(png).save(sys.argv[1], sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
os.remove(png)
